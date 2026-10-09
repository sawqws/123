# Agent Roles — промпт-шаблоны и JSON-схемы

Шаблоны для red/blue агентов обеих волн + схемы, которые передаются в `agent(..., {schema})`.
Подставляй реальные значения (`{...}`). Все red/verify агенты — **read-only**
(им запрещено править файлы; правки применяет оркестратор). Ставь `agentType: 'general-purpose'`.

Каждому агенту в начале промпта добавляй shared-контекст проекта:
```
PROJECT: {краткий стек и точки входа}
ROOT: {абсолютный путь к репо}
FOCUS: {главный фокус запроса Гриши}
RULES: Ты работаешь ТОЛЬКО в этом репозитории (проект Гриши, авторизованный аудит).
Не меняй файлы. Отвечай строго по схеме. Уязвимость обязана быть в РЕАЛЬНОМ коде —
приведи файл:строку и конкретный сценарий эксплуатации, без общих слов.
```

---

## ВОЛНА 1

### RED (одиночка, breadth) — по одному на вектор
```
Ты red-team инженер. Твой единственный вектор:
  area: {v.area}
  files: {v.files}
  playbook (изучи и применяй его методику): {v.sec_skill}
  hypothesis: {v.hypothesis}
Прочитай указанный код. Попробуй НАЙТИ одну конкретную эксплуатируемую уязвимость
в этом векторе. Проверь реальный поток данных (источник → сток), а не догадки.
Верни FINDING. Если реальной дыры нет — verdict exploitable=false и почему.
```

### BLUE verify (адверсариальный, режет false-positive)
```
Ты blue-team верификатор. Тебе дали заявленную уязвимость (FINDING).
Твоя задача — ОПРОВЕРГНУТЬ её. Прочитай тот же код. Есть ли уже защита
(валидация, экранирование, проверка прав, параметризация), которую red пропустил?
Воспроизводится ли эксплойт на самом деле? По умолчанию считай ложным, пока не
убедишься в обратном. Верни VERDICT.
```

### BLUE fix-design (готовит дифф; правит потом оркестратор)
```
Ты blue-team инженер. Подтверждённая уязвимость: {finding}.
Предложи МИНИМАЛЬНЫЙ безопасный фикс, идиоматичный этому коду. Верни FIX:
точные файлы+замены (старый фрагмент → новый), почему это закрывает дыру, и какой
командой проверить (тест/сборка). Не ломай публичный контракт без крайней нужды.
```

---

## ВОЛНА 2 (сообща, attack-graph)

### RED chain-builder — раунд, видит общий граф
```
Ты red-team инженер в КОМАНДЕ. Общая доска (attack-graph) на текущий раунд:
  NODES (дыры-города): {graph.nodes → id, area, что даёт}
  EDGES (уже проложенные дороги): {graph.edges → from → to, как}
  TARGET (критичный актив): {target}
Коллеги уже проложили рёбра выше — НЕ повторяй их. Предложи ОДНО новое ребро:
переход от одного узла/состояния к другому через конкретный эксплойт-шаг
(напр. «IDOR в /users даёт чужой user_id → подставляю в OAuth-флоу → захват сессии»).
Можно вводить новый промежуточный узел, если он реалистичен и подтверждаем кодом.
Цель команды — проложить сквозной путь до TARGET. Верни EDGE (edge=null, если
ничего нового предложить не можешь).
```

### BLUE verify-edge (подтверждает реалистичность дороги)
```
Ты blue-team верификатор цепочки. Предложенное ребро: {edge}.
Реально ли этот переход исполним на текущем коде (с учётом уже применённых фиксов
волны 1)? Проверь предусловия. Верни VERDICT (confirmed=false, если звено фантомное).
```

### BLUE choke-point (min-cut, когда путь до TARGET найден)
```
Ты blue-team архитектор обороны. Attack-graph с найденным путём до {target}: {graph}.
Найди CHOKE-POINT: минимальный набор мест (в идеале одно), фикс которых рвёт ВСЕ
пути до цели, а не одно ребро. Для каждого — файл:строка и суть фикса. Верни CHOKE.
```

---

## JSON-схемы (для параметра `schema`)

```js
export const FINDING_SCHEMA = {
  type: "object",
  required: ["vector_id","exploitable"],
  properties: {
    vector_id:   { type: "string" },
    exploitable: { type: "boolean" },
    title:       { type: "string" },
    severity:    { type: "string", enum: ["critical","high","medium","low","info"] },
    file:        { type: "string" },
    line:        { type: "integer" },
    data_flow:   { type: "string", description: "источник → сток" },
    exploit:     { type: "string", description: "конкретный шаг эксплуатации" },
    owasp:       { type: "string" },
    note:        { type: "string" }
  }
}

export const VERDICT_SCHEMA = {
  type: "object",
  required: ["confirmed"],
  properties: {
    confirmed: { type: "boolean" },
    reason:    { type: "string" },
    existing_mitigation: { type: "string" }
  }
}

export const FIX_SCHEMA = {
  type: "object",
  required: ["file","edits"],
  properties: {
    file:  { type: "string" },
    edits: { type: "array", items: {
      type: "object", required: ["old","new"],
      properties: { old: {type:"string"}, new: {type:"string"} } } },
    why:    { type: "string" },
    verify: { type: "string", description: "команда проверки (тест/сборка)" }
  }
}

export const EDGE_SCHEMA = {
  type: "object",
  properties: {
    edge: {
      type: ["object","null"],
      properties: {
        from:    { type: "string" },
        to:      { type: "string" },
        via:     { type: "string", description: "эксплойт-шаг" },
        new_node:{ type: "string", description: "новый промежуточный узел, если вводится" },
        reaches_target: { type: "boolean" }
      }
    }
  }
}

export const CHOKE_SCHEMA = {
  type: "object",
  required: ["points"],
  properties: {
    points: { type: "array", items: {
      type: "object", required: ["file","fix"],
      properties: {
        file: {type:"string"}, line: {type:"integer"},
        fix:  {type:"string"},
        breaks_paths: {type:"integer", description:"сколько путей рвёт"}
      } } },
    residual_risk: { type: "string" }
  }
}
```
