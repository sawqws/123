// Security Manager — Workflow-скелеты. АДАПТИРУЙ под конкретный проект.
// Запускается КАЖДАЯ волна отдельным вызовом Workflow (один script на волну).
// Схемы (FINDING_SCHEMA, VERDICT_SCHEMA, EDGE_SCHEMA, CHOKE_SCHEMA) и тексты
// промптов — из references/agent-roles.md; здесь они встроены компактно.
// Ограничения движка (важно): Date.now()/Math.random()/argless new Date() НЕДОСТУПНЫ —
// разнообразь агентов по index; таймстемпы для отчёта ставь ПОСЛЕ возврата, в главном цикле.
//
// ─────────────────────────────────────────────────────────────────────────────
// ВОЛНА 1 — breadth: независимые red по векторам → blue verify → blue fix-design.
// args: { root, project, focus, vectors:[{id,area,files,sec_skill,hypothesis}] }
// Возвращает: [{ ...finding, verdict, fix }]  — оркестратор применит fix последовательно.
// ─────────────────────────────────────────────────────────────────────────────
export const meta = {
  name: 'secmgr-wave1-breadth',
  description: 'Wave 1: independent red breadth sweep, blue verify + fix-design',
  phases: [{ title: 'Attack' }, { title: 'Verify' }, { title: 'FixDesign' }],
}

const CTX = `PROJECT: ${args.project}\nROOT: ${args.root}\nFOCUS: ${args.focus}\n` +
  `RULES: Работай ТОЛЬКО в этом репозитории (авторизованный аудит проекта Гриши). ` +
  `Не меняй файлы. Отвечай строго по схеме. Уязвимость — в реальном коде, дай файл:строку и сценарий.\n`

const FINDING_SCHEMA = { type:"object", required:["vector_id","exploitable"], properties:{
  vector_id:{type:"string"}, exploitable:{type:"boolean"}, title:{type:"string"},
  severity:{type:"string", enum:["critical","high","medium","low","info"]},
  file:{type:"string"}, line:{type:"integer"}, data_flow:{type:"string"},
  exploit:{type:"string"}, owasp:{type:"string"}, note:{type:"string"} } }
const VERDICT_SCHEMA = { type:"object", required:["confirmed"], properties:{
  confirmed:{type:"boolean"}, reason:{type:"string"}, existing_mitigation:{type:"string"} } }
const FIX_SCHEMA = { type:"object", required:["file","edits"], properties:{
  file:{type:"string"}, edits:{type:"array", items:{type:"object", required:["old","new"],
    properties:{ old:{type:"string"}, new:{type:"string"} }}},
  why:{type:"string"}, verify:{type:"string"} } }

const results = await pipeline(
  args.vectors,
  // Стадия 1 — RED (read-only, каждый сам за себя)
  (v) => agent(
    CTX + `Ты red-team инженер. Вектор: area=${v.area}; files=${JSON.stringify(v.files)}; ` +
    `playbook=${v.sec_skill}; hypothesis=${v.hypothesis}. Прочитай код, найди ОДНУ реальную ` +
    `эксплуатируемую уязвимость по этому вектору (проверь поток источник→сток). Верни FINDING.`,
    { label:`red:${v.id}`, phase:'Attack', agentType:'general-purpose', schema: FINDING_SCHEMA }
  ),
  // Стадия 2 — BLUE verify (адверсариально), только для заявленных дыр
  (finding, v) => (finding && finding.exploitable)
    ? agent(
        CTX + `Ты blue-team верификатор. Заявленная уязвимость: ${JSON.stringify(finding)}. ` +
        `Попробуй ОПРОВЕРГНУТЬ: есть ли уже защита, воспроизводится ли эксплойт? По умолчанию ложь. Верни VERDICT.`,
        { label:`verify:${v.id}`, phase:'Verify', agentType:'general-purpose', schema: VERDICT_SCHEMA }
      ).then(verdict => ({ ...finding, verdict }))
    : finding,
  // Стадия 3 — BLUE fix-design (только подтверждённые); дифф применит оркестратор
  (f) => (f && f.exploitable && f.verdict && f.verdict.confirmed)
    ? agent(
        CTX + `Ты blue-team инженер. Подтверждённая уязвимость: ${JSON.stringify(f)}. ` +
        `Предложи МИНИМАЛЬНЫЙ идиоматичный фикс. Верни FIX (old→new фрагменты + команда проверки).`,
        { label:`fix:${f.vector_id}`, phase:'FixDesign', agentType:'general-purpose', schema: FIX_SCHEMA }
      ).then(fix => ({ ...f, fix }))
    : f
)

return results.filter(Boolean).filter(f => f.exploitable && f.verdict && f.verdict.confirmed)


// ─────────────────────────────────────────────────────────────────────────────
// ВОЛНА 2 — depth/collaborative: attack-graph раундами (общая доска = «сообща»).
// Запускать ОТДЕЛЬНЫМ вызовом Workflow (свой meta/скрипт). args для неё:
//   { root, project, focus, target, holes:[{id,area,gives}], redPerRound, maxRounds }
// Возвращает: { graph:{nodes,edges}, path, chokes }.
// Скопируй блок ниже в отдельный script при запуске волны 2:
/*
export const meta = {
  name: 'secmgr-wave2-attackgraph',
  description: 'Wave 2: collaborative attack-graph chaining + blue choke-point',
  phases: [{ title: 'Chain' }, { title: 'VerifyEdge' }, { title: 'Defend' }],
}
const CTX = `PROJECT: ${args.project}\nROOT: ${args.root}\nFOCUS: ${args.focus}\nRULES: авторизованный аудит, не меняй файлы, строго по схеме.\n`
const EDGE_SCHEMA = { type:"object", properties:{ edge:{ type:["object","null"], properties:{
  from:{type:"string"}, to:{type:"string"}, via:{type:"string"},
  new_node:{type:"string"}, reaches_target:{type:"boolean"} }}}}
const VERDICT_SCHEMA = { type:"object", required:["confirmed"], properties:{
  confirmed:{type:"boolean"}, reason:{type:"string"} } }
const CHOKE_SCHEMA = { type:"object", required:["points"], properties:{ points:{type:"array", items:{
  type:"object", required:["file","fix"], properties:{ file:{type:"string"}, line:{type:"integer"},
  fix:{type:"string"}, breaks_paths:{type:"integer"} }}}, residual_risk:{type:"string"} } }

const graph = { nodes: args.holes.slice(), edges: [] }
const key = e => `${e.from}=>${e.to}:${(e.via||'').slice(0,40)}`
const seen = new Set()
const RED = Math.max(10, Math.min(18, args.redPerRound || 12))
let dry = 0, round = 0
const maxRounds = args.maxRounds || 6

while (dry < 2 && round < maxRounds && (!budget.total || budget.remaining() > 60000)) {
  round++
  const board = `NODES: ${JSON.stringify(graph.nodes)}\nEDGES: ${JSON.stringify(graph.edges)}\nTARGET: ${args.target}`
  // RED раунд — каждый видит общую доску, предлагает НОВОЕ ребро (разнообразим по i)
  const proposals = (await parallel(Array.from({length: RED}, (_, i) => () =>
    agent(CTX + `Ты red-team инженер в КОМАНДЕ (агент #${i}). Доска:\n${board}\n` +
      `Коллеги уже проложили EDGES выше — не повторяй. Предложи ОДНО новое ребро (эксплойт-переход) ` +
      `к цели, можно ввести new_node. Верни EDGE (edge=null если нечего добавить).`,
      { label:`chain#${round}.${i}`, phase:'Chain', agentType:'general-purpose', schema: EDGE_SCHEMA })
  ))).filter(Boolean).map(r => r.edge).filter(Boolean).filter(e => !seen.has(key(e)))

  if (!proposals.length) { dry++; log(`round ${round}: нет новых рёбер (dry ${dry})`); continue }

  // BLUE verify-edge — подтверждаем реалистичность каждого предложенного ребра
  const verified = (await parallel(proposals.map(e => () =>
    agent(CTX + `Ты blue-team верификатор цепочки. Ребро: ${JSON.stringify(e)}. ` +
      `Исполним ли переход на текущем коде (с учётом фиксов волны 1)? Верни VERDICT.`,
      { label:`vedge#${round}`, phase:'VerifyEdge', agentType:'general-purpose', schema: VERDICT_SCHEMA })
      .then(v => ({ e, ok: v && v.confirmed }))
  ))).filter(Boolean).filter(x => x.ok).map(x => x.e)

  if (!verified.length) { dry++; log(`round ${round}: рёбра не подтвердились (dry ${dry})`); continue }
  dry = 0
  for (const e of verified) { seen.add(key(e)); graph.edges.push(e)
    if (e.new_node && !graph.nodes.find(n => n.id === e.new_node)) graph.nodes.push({ id: e.new_node, area: 'derived' }) }
  if (verified.some(e => e.reaches_target)) { log(`round ${round}: путь до TARGET найден`); break }
}

// BLUE choke-point (min-cut) — где рвём ВСЕ дороги к цели
const chokes = await agent(CTX + `Ты blue-team архитектор обороны. Граф: ${JSON.stringify(graph)}. ` +
  `Найди CHOKE-POINT: минимальный набор фиксов, рвущий ВСЕ пути до ${args.target}. Верни CHOKE.`,
  { label:'choke', phase:'Defend', agentType:'general-purpose', schema: CHOKE_SCHEMA })

return { graph, chokes }
*/
