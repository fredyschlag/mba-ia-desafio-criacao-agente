# Residencial Aurora: assistente virtual

API em Python com Google ADK para os moradores reservarem áreas comuns, cancelarem as próprias reservas, autorizarem visitantes e tirarem dúvidas sobre o regulamento. O modelo conduz a conversa, e as regras críticas ficam no código.

## Arquitetura

```text
principal
├── reservas     sub_agent, acionado por transferência
├── visitantes   sub_agent, acionado por transferência
└── regulamento  AgentTool, acionado como tool
```

Os agentes estão em `src/aurora/agents.py`, as tools de dados em `src/aurora/tools.py` e o serviço de conversa em `src/aurora/assistant.py`.

### principal

Conversa com o morador e distribui o trabalho. Por ser o agente raiz, recebe as mensagens novas e decide entre responder, transferir para um especialista ou chamar o `regulamento`. Ele não tem tools de reservas ou visitantes, e suas instruções não contêm o regulamento, o índice de capítulos nem números de apartamento.

### reservas e visitantes

`reservas` consulta a agenda, lista, cria e cancela reservas do apartamento da sessão. `visitantes` lista e autoriza visitantes desse apartamento.

Os dois são `sub_agents` acionados por transferência, porque a confirmação precisa retomar no agente que a pediu. Ambos usam `disallow_transfer_to_parent=True` e `disallow_transfer_to_peers=True`. Com esses bloqueios, depois que o especialista responde, o Runner não o escolhe para a mensagem seguinte, e ela volta ao `principal`.

Esses especialistas não são `AgentTool` porque a sessão interna de um `AgentTool` fica em memória e é descartada ao fim da chamada. Uma confirmação pedida lá dentro não poderia ser retomada.

### regulamento

Responde dúvidas sobre o regulamento. É um `AgentTool` com `skip_summarization=True`, chamado pelo `principal`. Sua instrução contém só o índice de títulos gerado de `dados/regulamento.md`, e a tool `ler_capitulo` devolve um único capítulo por chamada.

Ele lê o capítulo em uma sessão interna, que é descartada. Só a resposta final entra nos eventos da conversa, e `skip_summarization=True` faz dela a resposta ao morador, sem outra chamada ao `principal`. Esse agente não pede confirmação, por isso pode rodar como `AgentTool`.

### Versão do ADK

O projeto fixa `google-adk==2.14.1`. Esta topologia, com retomada (`ResumabilityConfig(is_resumable=True)`) e `SqliteSessionService`, foi testada nesta versão. Nos testes do projeto, a mesma combinação falhou nas versões 2.2.0 e 2.9.1: a rota aceitava a resposta, mas a ação não executava.

### Caminho de uma confirmação

1. O modelo chama `reservar_area` ou `autorizar_visitante`. Quando a ação exige confirmação, o ADK grava nos eventos um pedido `adk_request_confirmation` e não executa a tool.
2. `pending_confirmations` reconstrói a pendência a partir dos eventos, e a API a devolve em `confirmacoes_pendentes`.
3. O morador responde em `POST /sessoes/{session_id}/confirmacoes`.
4. `answer_confirmation` envia a resposta ao Runner com o `invocation_id` original, e o ADK retoma no especialista que pediu.
5. `finish_confirmed_action` encerra a execução sem resumo do modelo. Em uma negação, ele troca o resultado por `negada_pelo_morador`.
6. `confirmation_reply` monta o texto da resposta em código, a partir do status da tool. O modelo não é chamado.

### Modelo

Todos os agentes usam `gemini-3.5-flash-lite`, definido em `src/aurora/config.py`. Cada chamada faz até 6 tentativas, com espera exponencial, nas respostas 429, 500, 502, 503 e 504. Esgotadas as tentativas, a API responde 503 sem detalhes do provedor.

## Garantias

### Garantia 1: cobrança ou acesso só com confirmação

**Arquivo:** `src/aurora/tools.py`

```python
    FunctionTool(reservar_area, require_confirmation=reservation_requires_confirmation),
```

```python
    FunctionTool(autorizar_visitante, require_confirmation=visitor_requires_confirmation),
```

```python
    return _is_valid_area(area) and _is_valid_date(data) and area_fees()[area] > 0
```

```python
def _is_confirmed(tool_context: ToolContext) -> bool:
    confirmation = tool_context.tool_confirmation
    return confirmation is not None and confirmation.confirmed is True
```

**Arquivo:** `src/aurora/assistant.py`

```python
def pending_confirmations(events: list[Event]) -> dict[str, PendingConfirmation]:
```

```python
    async def answer_confirmation(
        self, session_id: str, confirmation_id: str, approved: bool
    ) -> dict[str, Any]:
        await self._load(session_id)
        async with self._locks[session_id]:
            session = await self._load(session_id)
            confirmation = pending_confirmations(session.events).get(confirmation_id)
            if confirmation is None:
                raise ConfirmationNotPending
```

**Por que não depende do modelo:** o modelo só propõe a chamada. Quem decide se ela pausa é o predicado, e a aprovação só chega pela rota de confirmações, nunca pelo texto de uma mensagem. Mesmo que a função seja chamada sem passar por essa pausa, `_is_confirmed` impede a gravação de uma ação com taxa ou de um visitante.

O ADK reavalia o predicado na retomada. Por isso ele usa só os argumentos da chamada, que não mudam, e a taxa de `dados/areas.json`, nunca a agenda. Uma data que ficou ocupada ou livre entre o pedido e a resposta não muda a exigência de confirmação.

A rota responde 409 para qualquer `id` que não esteja em aberto nos eventos daquela sessão, inclusive um `id` inexistente, de outra sessão ou já respondido. Nesse caso, o Runner não é chamado e nada é executado.

### Garantia 2: cada sessão pertence a um apartamento

**Arquivo:** `src/aurora/assistant.py`

```python
            app_name=APP_NAME, user_id=USER_ID, state={"apartamento": apartment}
```

**Arquivo:** `src/aurora/tools.py`

```python
def _session_apartment(tool_context: ToolContext) -> str | None:
    apartment = tool_context.state.get("apartamento")
    if isinstance(apartment, str) and apartment in apartment_numbers():
        return apartment
    return None
```

```python
    return {"status": "ocupada" if taken else "livre"}
```

**Arquivo:** `src/aurora/repository.py`

```python
            "WHERE apartment = ? AND area = ? AND date = ? AND status = 'active'",
```

**Por que não depende do modelo:** nenhuma tool recebe apartamento como parâmetro, então o modelo não tem como escolher outro. O valor é gravado no state na criação da sessão e lido pelas tools a partir dali. Se ele faltar, a tool recusa.

As listagens e o cancelamento filtram pelo apartamento da sessão. Cancelar uma reserva de outro apartamento devolve o mesmo `nao_encontrada` de uma reserva inexistente. Uma consulta válida de agenda devolve só `livre` ou `ocupada`, sem código, apartamento ou morador.

### Garantia 3: nada se perde no reinício

**Arquivo:** `src/aurora/assistant.py`

```python
        self.session_service = SqliteSessionService(db_path=str(SESSIONS_DB))
```

```python
def pending_confirmations(events: list[Event]) -> dict[str, PendingConfirmation]:
    """Rebuilds open confirmations exclusively from persisted events."""
```

**Arquivo:** `src/aurora/agents.py`

```python
        resumability_config=ResumabilityConfig(is_resumable=True),
```

**Arquivo:** `src/aurora/database.py`

```python
        if seeded is None:
            _insert_initial_rows(transaction)
```

**Por que não depende do modelo:** sessões e eventos ficam em `.aurora/sessoes.sqlite`, e reservas e visitantes em `.aurora/condominio.sqlite`. Nenhuma pendência é guardada em memória: ela é reconstruída dos eventos persistidos a cada requisição, e o `invocation_id` original vem desses mesmos eventos. Assim, uma confirmação aberta antes de parar a API com Ctrl+C pode ser respondida depois da nova subida. Fora do comando de restauração, os dados de `dados/` são carregados uma única vez, então um reinício não recria registros cancelados.

### Garantia 4: o regulamento é consultado, não carregado

**Arquivo:** `src/aurora/regulation.py`

```python
    return {"status": "ok", "capitulo": chapters[numero - 1].text}
```

**Arquivo:** `src/aurora/agents.py`

```python
        tools=[FunctionTool(ler_capitulo)],
```

```python
        tools=[AgentTool(agent=regulation, skip_summarization=True)],
```

**Por que não depende do modelo:** `ler_capitulo` só existe dentro do agente `regulamento`, que roda em uma sessão interna descartada ao fim da chamada. Por isso, as chamadas a `ler_capitulo` e o texto dos capítulos lidos não entram nos eventos da conversa, seja qual for o capítulo escolhido. O `principal` também não recebe o regulamento nas instruções.

Nos eventos entram a chamada à tool `regulamento`, com o pedido enviado pelo `principal`, e a resposta final desse agente. Essa resposta é redigida pelo modelo: a instrução manda usar só o capítulo pertinente e não transcrever artigos, mas o código não filtra esse texto. Se o primeiro capítulo não trouxer a resposta, o agente pode ler outro.

### Garantia 5: dois moradores, uma reserva

**Arquivo:** `src/aurora/database.py`

```python
    CREATE UNIQUE INDEX IF NOT EXISTS ux_reservations_active_area_date
    ON reservations (area, date) WHERE status = 'active'
```

```python
        connection.execute("BEGIN IMMEDIATE")
```

**Arquivo:** `src/aurora/repository.py`

```python
            "ON CONFLICT (area, date) WHERE status = 'active' DO NOTHING",
```

```python
        if inserted.rowcount == 0:
            transaction.execute("DELETE FROM issued_codes WHERE code = ?", (code,))
            return {"status": "ocupada"}
```

**Por que não depende do modelo:** a exclusividade vale no instante da gravação, e não em uma consulta feita antes. A transação pega a trava de escrita do SQLite antes do `INSERT`, e o índice único parcial impede uma segunda reserva ativa para a mesma área e data. Quem perde a disputa recebe o resultado normal `ocupada`, sem exceção e sem dados do vencedor. Uma reserva cancelada fica registrada, mas deixa de ocupar a data, porque o índice cobre só as ativas.

## Como rodar

### Pré-requisitos

- Python 3.12 ou superior
- [uv](https://docs.astral.sh/uv/)
- Chave do [Google AI Studio](https://aistudio.google.com/)

### Passo a passo

Crie o arquivo de ambiente:

```bash
cp .env.example .env
```

Abra o `.env` e preencha, depois do sinal de igual, a sua chave em `GOOGLE_API_KEY`, a única variável necessária:

```dotenv
GOOGLE_API_KEY=
```

Instale as dependências:

```bash
uv sync
```

Restaure os dados iniciais:

```bash
uv run aurora-restaurar
```

A restauração exige a API parada. Ela volta reservas e visitantes ao estado de `dados/` e apaga as sessões. Se a API estiver respondendo na porta 8000, o comando recusa e não altera nada.

Suba a API:

```bash
uv run aurora-api
```

A API responde em `http://localhost:8000` e para com Ctrl+C. Ela não depende de banco ou outro serviço externo para subir, porque os dados ficam em SQLite, na pasta `.aurora/`. As rotas de conversa usam a API do Gemini.

### Limitações conhecidas

- Em limite persistente do Gemini, uma chamada pode esperar cerca de 1 minuto e terminar em 503.
- Se isso ocorrer ao redigir a resposta de uma ação sem confirmação, a ação já pode estar gravada.
- O texto redigido pelo modelo pode errar. As rotas de verificação, `GET /apartamentos/{numero}/reservas` e `GET /apartamentos/{numero}/visitantes`, são a fonte dos dados.
