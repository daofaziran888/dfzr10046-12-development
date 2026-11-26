<template>
  <section class="delegation-panel" data-testid="segment-delegation-panel">
    <div class="delegation-head">
      <div>
        <h3>分段代办授权</h3>
        <p>
          维度建档人员可把某个维度的一段号码分配工作委托给接班人；授权结论同步到「代理键待办」。
          查看分段与写入均按维度归属控制，撤销后接班人保留只读历史记录。
        </p>
      </div>
      <div class="operator-switch" data-testid="operator-switcher">
        <span>当前操作员</span>
        <button
          v-for="name in quickOperators"
          :key="name"
          type="button"
          class="btn"
          :class="{ primary: operator === name }"
          :data-testid="`operator-${name}`"
          @click="operator = name"
        >
          {{ name }}
        </button>
        <input
          v-model="operator"
          class="operator-input"
          aria-label="操作员"
          data-testid="operator-custom"
          placeholder="输入操作员"
        />
      </div>
    </div>

    <div v-if="feedback.text" class="feedback" :class="feedback.tone" data-testid="delegation-feedback">
      {{ feedback.text }}
    </div>

    <div class="delegation-grid">
      <form class="card create-delegation" data-testid="delegation-form" @submit.prevent="createDelegation">
        <h4>新增委托（甲 → 接班人）</h4>
        <label>
          <span>委托人（维度建档人/当前操作员）</span>
          <input
            v-model="operator"
            aria-label="委托人"
            data-testid="delegation-owner"
            placeholder="甲"
          />
        </label>
        <label>
          <span>所属维度</span>
          <input
            v-model="createForm.dimension"
            list="dimension-options"
            required
            aria-label="所属维度"
            data-testid="delegation-dimension"
            placeholder="例如：客户维度"
          />
          <datalist id="dimension-options">
            <option v-for="item in dimensionOptions" :key="item" :value="item" />
          </datalist>
        </label>
        <div class="two-col">
          <label>
            <span>起始编号</span>
            <input v-model.number="createForm.start" type="number" required aria-label="起始编号" data-testid="delegation-start" />
          </label>
          <label>
            <span>结束编号</span>
            <input v-model.number="createForm.end" type="number" required aria-label="结束编号" data-testid="delegation-end" />
          </label>
        </div>
        <label>
          <span>接班人</span>
          <input v-model="createForm.delegate" required aria-label="接班人" data-testid="delegation-delegate" placeholder="例如：乙" />
        </label>
        <button class="btn primary" type="submit" :disabled="loading" data-testid="submit-delegation">
          新增分段代办授权
        </button>
        <button class="btn ghost" type="button" data-testid="setup-conflict-scenario" @click="setupScenario">
          一键准备：甲委托 100-109 + 其他维度同号 104 对照
        </button>
      </form>

      <div class="card" data-testid="todo-panel">
        <h4>代理键待办（授权结论同步）</h4>
        <p v-if="!todos.length" class="muted">当前操作员暂无代理键待办。</p>
        <ul v-else class="todo-list">
          <li v-for="todo in todos" :key="todo.id" :data-testid="`todo-${todo.delegation_id}`">
            <strong>{{ todo.title }}</strong>
            <span class="badge">{{ todo.status }}</span>
            <p>{{ todo.conclusion }}</p>
            <p class="muted">
              可用号码：<span data-testid="todo-available">{{ (todo.available_numbers || []).join('、') || '无' }}</span>
            </p>
            <button class="link" type="button" @click="selectDelegation(todo.delegation_id)">打开该分段</button>
          </li>
        </ul>
      </div>
    </div>

    <div class="card delegation-list-card">
      <div class="card-title-row">
        <h4>分段授权列表</h4>
        <button class="btn ghost" type="button" data-testid="refresh-delegations" @click="refresh()">刷新</button>
      </div>
      <table class="data-table" data-testid="delegation-list">
        <thead>
          <tr>
            <th>授权编号</th><th>所属维度</th><th>号码段</th><th>委托人</th><th>接班人</th>
            <th>状态/版本</th><th>可用号码</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in delegations" :key="item.id" :data-testid="`delegation-row-${item.id}`">
            <td>{{ item.delegation_no }}</td>
            <td>{{ item.dimension }}</td>
            <td>{{ item.start }}-{{ item.end }}</td>
            <td>{{ item.owner }}</td>
            <td>{{ item.delegate }}</td>
            <td>{{ item.status }} / v{{ item.version }}</td>
            <td>{{ (item.available_numbers || []).join('、') || '无' }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`select-delegation-${item.id}`" @click="selectDelegation(item.id)">
                填写/核对
              </button>
              <button
                v-if="item.permissions?.can_revoke"
                class="link danger"
                type="button"
                :data-testid="`revoke-${item.id}`"
                @click="revokeDelegation(item.id)"
              >
                撤销代办（取消代办）
              </button>
            </td>
          </tr>
          <tr v-if="!delegations.length">
            <td colspan="8" class="empty-state">暂无当前操作员可见的分段授权。</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="selected" class="workspace-grid">
      <div class="card fill-card" data-testid="fill-panel">
        <div class="card-title-row">
          <h4>填写并确认分段：{{ selected.delegation_no }}</h4>
          <span class="badge" :class="{ revoked: selected.status === '已撤销' }">{{ selected.status }} / v{{ selected.version }}</span>
        </div>
        <p><strong>所属维度：</strong>{{ selected.dimension }}；<strong>号码段：</strong>{{ selected.start }}-{{ selected.end }}</p>

        <p class="permission-banner" :data-testid="`permission-${operator}`">
          <template v-if="selected.permissions?.is_owner">
            当前操作员 {{ operator }} 是维度归属人，可查看、撤销并继续填写。
          </template>
          <template v-else-if="selected.permissions?.read_only">
            代办已撤销：接班人 {{ operator }} 现在只能只读查看旧办理记录，不能继续填写。
          </template>
          <template v-else-if="selected.permissions?.is_delegate && selected.permissions?.can_write">
            当前接班人 {{ operator }} 可填写并确认分段。
          </template>
          <template v-else>
            当前操作员 {{ operator }} 对该维度只有受限或无权限视图，越权写入会被后端拒绝。
          </template>
        </p>

        <div class="number-grid" data-testid="number-grid">
          <button
            v-for="n in selected.numbers"
            :key="n"
            type="button"
            class="number-chip"
            :class="{
              used: selected.used_numbers.includes(n),
              available: selected.available_numbers.includes(n),
              draft: selected.draft_numbers.includes(n),
            }"
            :data-testid="`number-chip-${n}`"
            @click="selectNumber(n)"
          >
            {{ n }}
            <span v-if="selected.used_numbers.includes(n)">已正式使用</span>
            <span v-else-if="fillForm.number === n">填写中</span>
            <span v-else>可用</span>
          </button>
        </div>
        <p class="available-line">
          刷新或超时后回到未完成表单，可在此核对哪些号码还能用；当前仍可使用的号码：
          <strong data-testid="available-numbers">{{ selected.available_numbers.join('、') || '无' }}</strong>
        </p>

        <form class="fill-form" data-testid="fill-form" @submit.prevent="confirmNumber">
          <label>
            <span>办理号码</span>
            <input v-model.number="fillForm.number" type="number" aria-label="办理号码" data-testid="fill-number" @change="restoreFormForSelected()" />
          </label>
          <label>
            <span>自然键/分配对象</span>
            <input v-model="fillForm.naturalKey" aria-label="自然键" data-testid="fill-natural-key" placeholder="例如 NK-104" @input="saveLocalDraft" />
          </label>
          <label>
            <span>填写说明</span>
            <input v-model="fillForm.content" aria-label="填写说明" data-testid="fill-content" placeholder="该号码分配给哪个客户/单据" @input="saveLocalDraft" />
          </label>
          <div class="button-row">
            <button class="btn" type="button" data-testid="save-draft" :disabled="!selected.permissions?.can_write" @click="saveDraft">
              暂存未完成表单
            </button>
            <button class="btn primary" type="submit" data-testid="confirm-number" :disabled="submitting || !selected.permissions?.can_write">
              填写并确认分段
            </button>
          </div>
          <p class="muted">草稿只用于刷新/超时后恢复，不会正式占用号码；只有“确认”成功才形成唯一使用记录。</p>
        </form>
      </div>

      <div class="card records-card" data-testid="records-panel">
        <h4>旧办理记录与历史分配</h4>
        <p class="muted">撤销代办不会改写这些已确认记录；接班人撤销后仍可只读核对。</p>
        <table class="data-table" data-testid="usage-records">
          <thead><tr><th>维度</th><th>号码</th><th>自然键</th><th>办理人</th><th>状态</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="record in usage" :key="record.id" :data-testid="`usage-${record.dimension_key}-${record.number}`">
              <td>{{ record.所属维度 }}</td>
              <td>{{ record.number }}</td>
              <td>{{ record.natural_key || '—' }}</td>
              <td>{{ record.operator }}</td>
              <td>{{ record.status }}</td>
              <td>{{ record.confirmed_at }}</td>
            </tr>
            <tr v-if="!usage.length"><td colspan="6" class="empty-state">暂无已确认使用记录。</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="card reconcile-card" data-testid="reconcile-panel">
      <h4>冲突核对 / 同号维度对照</h4>
      <div class="reconcile-form">
        <label><span>核对维度</span><input v-model="checkForm.dimension" aria-label="核对维度" data-testid="check-dimension" /></label>
        <label><span>号码</span><input v-model.number="checkForm.number" type="number" aria-label="核对号码" data-testid="check-number" /></label>
        <button class="btn primary" type="button" data-testid="inspect-number" @click="inspectNumber">核对号码最终状态</button>
      </div>
      <div v-if="inspection" class="inspection-result" data-testid="inspection-result">
        <p><strong>{{ inspection.message }}</strong></p>
        <p data-testid="inspection-used">号码 {{ inspection.number }} 是否已正式使用：{{ inspection.officially_used ? '是' : '否' }}</p>
        <p data-testid="inspection-reassign">是否还能再次分配：{{ inspection.can_allocate_again ? '是（未使用时可由有权操作员分配）' : '否（已正式使用，不能再次分配）' }}</p>
        <p data-testid="inspection-writers">有权继续填写的操作员：{{ (inspection.continuable_operators || []).join('、') || '无' }}</p>
        <p class="muted">{{ inspection.write_reason }}</p>
      </div>

      <div class="reconcile-form control-row">
        <label><span>其他维度（同号对照）</span><input v-model="controlDimension" aria-label="对照维度" data-testid="control-dimension" /></label>
        <button class="btn" type="button" data-testid="inspect-control" @click="inspectControl">核对对照维度 104</button>
        <button
          class="btn danger"
          type="button"
          data-testid="cross-dimension-denied"
          :disabled="!selected"
          @click="attemptCrossDimension"
        >
          让乙用当前授权向对照维度确认 104（应拒绝）
        </button>
        <button
          class="btn"
          type="button"
          data-testid="race-conflict"
          :disabled="!selected"
          @click="runRace"
        >
          并发提交核对：乙确认 104 × 甲撤销
        </button>
      </div>
      <div v-if="controlInspection" class="inspection-result control-result" data-testid="control-result">
        <strong>对照维度：</strong>{{ controlInspection.message }}
      </div>
      <div v-if="crossResult" class="feedback error" data-testid="cross-dimension-result">{{ crossResult }}</div>
      <div v-if="raceResult" class="inspection-result race-result" data-testid="race-result">
        <p><strong>并发结果：</strong>{{ raceResult.message }}；成功提交数 {{ raceResult.success_count }}（必须为 1）。</p>
        <p>乙确认：{{ raceResult.confirm_result?.ok ? '成功' : '失败' }} — {{ raceResult.confirm_result?.message }}</p>
        <p>甲撤销：{{ raceResult.revoke_result?.ok ? '成功' : '失败' }} — {{ raceResult.revoke_result?.message }}</p>
        <p v-if="raceResult.settled">冲突后处理：{{ raceResult.settled.message }}</p>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { delegationApi, DelegationApiError, listEntries } from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const emit = defineEmits<{ changed: [] }>()

const quickOperators = ['甲', '乙', '丙']
const operator = ref(localStorage.getItem('sk-current-operator') || '甲')
watch(operator, (value) => localStorage.setItem('sk-current-operator', value))

const dimensions = ref<EntryRow[]>([])
const dimensionOptions = computed(() => {
  const names = dimensions.value.map((row) => String(row.维度名称 || '')).filter(Boolean)
  return Array.from(new Set([...names, '客户维度', '产品维度', '分段代办主维度', '同号对照维度']))
})

const delegations = ref<any[]>([])
const todos = ref<any[]>([])
const usage = ref<any[]>([])
const selectedId = ref<number | string | null>(
  localStorage.getItem('sk-selected-delegation') ? Number(localStorage.getItem('sk-selected-delegation')) : null,
)
const selected = computed(() => delegations.value.find((item) => String(item.id) === String(selectedId.value)) || null)

const loading = ref(false)
const submitting = ref(false)
const feedback = ref<{ tone: 'ok' | 'error' | 'info'; text: string }>({ tone: 'info', text: '' })
const createForm = ref({ dimension: '客户维度', start: 100, end: 109, delegate: '乙' })
const fillForm = ref<{ number: number; naturalKey: string; content: string; version?: number }>({
  number: 104, naturalKey: '', content: '',
})
const checkForm = ref({ dimension: '客户维度', number: 104 })
const controlDimension = ref('产品维度')
const inspection = ref<any>(null)
const controlInspection = ref<any>(null)
const crossResult = ref('')
const raceResult = ref<any>(null)

watch(selectedId, (value) => {
  if (value === null || Number.isNaN(value)) localStorage.removeItem('sk-selected-delegation')
  else localStorage.setItem('sk-selected-delegation', String(value))
})

watch(operator, async () => {
  // 权限是按当前操作员计算的；切换甲/乙/丙后必须重新拉取可见范围和 permissions。
  if (!delegations.value.length && !selectedId.value) return
  await refresh()
  if (checkForm.value.dimension && checkForm.value.number) await inspectNumber()
})

function showError(error: unknown, fallback: string) {
  feedback.value = {
    tone: 'error',
    text: error instanceof DelegationApiError ? error.message : error instanceof Error ? error.message : fallback,
  }
}

function draftMapKey(id: number | string) {
  return `sk-draft-map:${operator.value}:${id}`
}

function lastNumberKey(id: number | string) {
  return `sk-last-number:${operator.value}:${id}`
}

function readDraftMap(id: number | string): Record<string, any> {
  try {
    return JSON.parse(localStorage.getItem(draftMapKey(id)) || '{}') as Record<string, any>
  } catch {
    return {}
  }
}

function writeDraftMap(id: number | string, map: Record<string, any>) {
  localStorage.setItem(draftMapKey(id), JSON.stringify(map))
}

function saveLocalDraft() {
  if (!selected.value) return
  const map = readDraftMap(selected.value.id)
  map[String(fillForm.value.number)] = {
    number: fillForm.value.number,
    naturalKey: fillForm.value.naturalKey,
    content: fillForm.value.content,
    savedAt: new Date().toISOString(),
  }
  writeDraftMap(selected.value.id, map)
  localStorage.setItem(lastNumberKey(selected.value.id), String(fillForm.value.number))
}

function restoreFormForSelected(preferredNumber?: number) {
  const d = selected.value
  if (!d) return
  fillForm.value.version = d.version
  const numbers: number[] = d.numbers || []
  const storedNumber = Number(localStorage.getItem(lastNumberKey(d.id)) || fillForm.value.number)
  if (preferredNumber !== undefined && numbers.includes(preferredNumber)) {
    fillForm.value.number = preferredNumber
  } else if (numbers.includes(storedNumber)) {
    fillForm.value.number = storedNumber
  }
  if (!numbers.includes(Number(fillForm.value.number))) {
    fillForm.value.number = d.available_numbers?.[0] ?? numbers[0] ?? 104
  }
  localStorage.setItem(lastNumberKey(d.id), String(fillForm.value.number))
  const draft = readDraftMap(d.id)[String(fillForm.value.number)]
  if (draft) {
    fillForm.value.naturalKey = draft.naturalKey || ''
    fillForm.value.content = draft.content || ''
    feedback.value = { tone: 'info', text: `已恢复刷新/超时前未完成的 ${fillForm.value.number} 表单；可用号码已按后端记录重新计算。` }
  } else {
    fillForm.value.naturalKey = ''
    fillForm.value.content = ''
    void restoreBackendDraft()
  }
}

async function restoreBackendDraft() {
  if (!selected.value) return
  const delegationId = selected.value.id
  const number = fillForm.value.number
  try {
    const result = await delegationApi.getDraft(operator.value, delegationId, number)
    const draft = result.draft
    if (
      draft
      && String(selectedId.value) === String(delegationId)
      && Number(fillForm.value.number) === Number(draft.number)
    ) {
      fillForm.value.naturalKey = String(draft.natural_key || draft.naturalKey || '')
      fillForm.value.content = String(draft.content || '')
      saveLocalDraft()
    }
  } catch {
    // 没有后端草稿时保持空白表单，可用号码仍由授权数据明确给出。
  }
}

async function loadDimensions() {
  try {
    const page = await listEntries('dim_entry', { size: '200' })
    dimensions.value = page.items
  } catch {
    dimensions.value = []
  }
}

async function refresh(options: { restore?: boolean } = { restore: true }) {
  loading.value = true
  try {
    const [delegationPage, todoPage, usagePage] = await Promise.all([
      delegationApi.list(operator.value),
      delegationApi.todos(operator.value),
      delegationApi.usage(operator.value),
    ])
    delegations.value = delegationPage.items
    todos.value = todoPage.items
    usage.value = usagePage.items
    if (!selectedId.value && delegations.value.length) {
      selectedId.value = delegations.value[0].id
    }
    if (options.restore !== false) restoreFormForSelected()
    emit('changed')
  } catch (error) {
    showError(error, '分段代办数据读取失败')
  } finally {
    loading.value = false
  }
}

async function createDelegation() {
  loading.value = true
  feedback.value = { tone: 'info', text: '' }
  try {
    const result = await delegationApi.create(operator.value, { ...createForm.value }) as any
    selectedId.value = result.delegation.id
    checkForm.value.dimension = createForm.value.dimension
    feedback.value = { tone: 'ok', text: result.message }
    fillForm.value.naturalKey = ''
    fillForm.value.content = ''
    await refresh()
  } catch (error) {
    showError(error, '新增授权失败')
  } finally {
    loading.value = false
  }
}

function selectDelegation(id: number | string) {
  selectedId.value = id
  raceResult.value = null
  crossResult.value = ''
  restoreFormForSelected()
}

function selectNumber(n: number) {
  saveLocalDraft()
  fillForm.value.number = n
  if (selected.value) localStorage.setItem(lastNumberKey(selected.value.id), String(n))
  restoreFormForSelected(n)
}

async function saveDraft() {
  if (!selected.value) return
  saveLocalDraft()
  try {
    const result = await delegationApi.saveDraft(operator.value, selected.value.id, {
      number: fillForm.value.number,
      natural_key: fillForm.value.naturalKey,
      content: fillForm.value.content,
      version: fillForm.value.version,
    }) as any
    feedback.value = { tone: 'ok', text: result.message || '草稿已暂存' }
    await refresh({ restore: false })
    fillForm.value.version = selected.value.version
  } catch (error) {
    showError(error, '草稿暂存失败')
  }
}

async function confirmNumber() {
  if (!selected.value) return
  submitting.value = true
  feedback.value = { tone: 'info', text: '' }
  try {
    const result = await delegationApi.confirm(operator.value, selected.value.id, {
      number: fillForm.value.number,
      natural_key: fillForm.value.naturalKey,
      content: fillForm.value.content,
      version: selected.value.version,
    }) as any
    feedback.value = { tone: 'ok', text: result.message }
    const map = readDraftMap(selected.value.id)
    delete map[String(fillForm.value.number)]
    writeDraftMap(selected.value.id, map)
    fillForm.value.naturalKey = ''
    fillForm.value.content = ''
    checkForm.value.dimension = selected.value.dimension
    checkForm.value.number = fillForm.value.number
    await refresh()
    await inspectNumber()
  } catch (error) {
    if (error instanceof DelegationApiError) {
      feedback.value = { tone: 'error', text: error.message }
      if (error.data?.delegation) {
        const latest = error.data.delegation as any
        const index = delegations.value.findIndex((d) => d.id === latest.id)
        if (index >= 0) delegations.value[index] = latest
        fillForm.value.version = latest.version
      } else {
        await refresh({ restore: false })
      }
      if (checkForm.value.number === fillForm.value.number) await inspectNumber()
    } else {
      showError(error, '确认失败')
    }
  } finally {
    submitting.value = false
  }
}

async function revokeDelegation(id: number | string, isRetry = false) {
  const current = delegations.value.find((d) => String(d.id) === String(id))
  try {
    const result = await delegationApi.revoke(operator.value, id, current?.version) as any
    feedback.value = { tone: 'ok', text: result.message }
    await refresh()
    if (checkForm.value.dimension && checkForm.value.number) await inspectNumber()
  } catch (error) {
    const errorCode = error instanceof DelegationApiError ? String(error.data.code || '') : ''
    if (error instanceof DelegationApiError && (error.status === 409 || errorCode === 'version_conflict') && !isRetry) {
      await refresh({ restore: false })
      const latest = delegations.value.find((d) => String(d.id) === String(id))
      feedback.value = { tone: 'info', text: '撤销遇到并发版本变化，已刷新到最新状态并按最新版本再次撤销。' }
      await revokeDelegation(id, true)
      if (latest) fillForm.value.version = latest.version
      return
    }
    showError(error, '撤销失败')
    await refresh({ restore: false })
  }
}

async function inspectNumber() {
  try {
    inspection.value = await delegationApi.inspect(
      operator.value, checkForm.value.dimension, checkForm.value.number,
    )
  } catch (error) {
    showError(error, '号码核对失败')
  }
}

async function inspectControl() {
  try {
    controlInspection.value = await delegationApi.inspect(operator.value, controlDimension.value, 104)
  } catch (error) {
    showError(error, '对照维度核对失败')
  }
}

async function attemptCrossDimension() {
  if (!selected.value) return
  crossResult.value = ''
  try {
    const result = await delegationApi.crossAttempt('乙', selected.value.id, {
      dimension: controlDimension.value,
      number: 104,
      version: selected.value.version,
    })
    crossResult.value = `意外成功：${JSON.stringify(result)}`
  } catch (error) {
    crossResult.value = error instanceof Error ? error.message : '跨维度操作被拒绝'
  }
}

async function setupScenario() {
  loading.value = true
  try {
    operator.value = '甲'
    const result = await delegationApi.setupScenario('甲') as any
    selectedId.value = result.delegation.id
    createForm.value = { dimension: result.primary_dimension, start: 100, end: 109, delegate: '乙' }
    checkForm.value = { dimension: result.primary_dimension, number: 104 }
    controlDimension.value = result.control_dimension
    inspection.value = null
    controlInspection.value = null
    raceResult.value = null
    feedback.value = { tone: 'ok', text: result.message || '标准场景已准备' }
    await refresh()
    await inspectNumber()
    await inspectControl()
  } catch (error) {
    showError(error, '场景准备失败')
  } finally {
    loading.value = false
  }
}

async function runRace() {
  if (!selected.value) return
  loading.value = true
  try {
    raceResult.value = await delegationApi.race('甲', selected.value.id)
    operator.value = '甲'
    checkForm.value = { dimension: selected.value.dimension, number: 104 }
    await refresh()
    await inspectNumber()
    await inspectControl()
  } catch (error) {
    showError(error, '并发自检失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadDimensions()
  await refresh()
  if (checkForm.value.dimension) await inspectNumber()
})
</script>

<style scoped>
.delegation-panel { margin: 12px 0 16px; padding: 14px; border: 1px solid var(--border); border-radius: 10px; background: #fbfdff; }
.delegation-head, .card-title-row, .operator-switch, .button-row, .reconcile-form { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.delegation-head { justify-content: space-between; }
.delegation-head h3, .card h4 { margin: 0 0 8px; }
.delegation-head p { max-width: 900px; color: var(--muted); font-size: 13px; margin: 0; }
.operator-switch span { font-size: 13px; color: var(--muted); }
.operator-input { width: 110px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.feedback { margin: 10px 0; padding: 8px 12px; border-radius: 6px; font-size: 13px; background: #eff6ff; color: #1d4ed8; }
.feedback.ok { background: #ecfdf3; color: #027a48; }
.feedback.error { background: #fef3f2; color: #b42318; }
.delegation-grid, .workspace-grid { display: grid; grid-template-columns: minmax(280px, 0.9fr) minmax(360px, 1.1fr); gap: 12px; margin-top: 12px; }
.workspace-grid { grid-template-columns: minmax(360px, 1fr) minmax(420px, 1.2fr); }
.card { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.card label { display: block; margin-bottom: 8px; font-size: 13px; }
.card label span { display: block; color: var(--muted); margin-bottom: 4px; }
.card input { width: 100%; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.delegation-list-card, .reconcile-card { margin-top: 12px; }
.todo-list { margin: 0; padding-left: 18px; }
.todo-list li { margin-bottom: 10px; }
.todo-list p { margin: 4px 0; font-size: 13px; }
.badge { display: inline-block; margin-left: 6px; padding: 2px 8px; border-radius: 999px; background: #dbeafe; color: #1d4ed8; font-size: 12px; }
.badge.revoked { background: #fee4e2; color: #b42318; }
.muted { color: var(--muted); font-size: 12px; }
.number-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(86px, 1fr)); gap: 6px; margin: 10px 0; }
.number-chip { border: 1px solid var(--border); background: #fff; border-radius: 6px; padding: 7px 4px; cursor: pointer; font-size: 13px; }
.number-chip span { display: block; font-size: 11px; color: var(--muted); }
.number-chip.available { border-color: #73d09a; background: #f0fdf4; }
.number-chip.used { background: #f2f4f7; color: #667085; text-decoration: line-through; cursor: not-allowed; }
.number-chip.used span { text-decoration: none; }
.number-chip.draft { border-color: #f79009; background: #fffaeb; }
.available-line { font-size: 13px; }
.fill-form { margin-top: 10px; }
.button-row { margin: 8px 0; }
.btn.danger, .link.danger { color: #b42318; }
.reconcile-form label { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 13px; }
.reconcile-form label span { color: var(--muted); }
.reconcile-form input { width: 130px; padding: 7px 9px; border: 1px solid var(--border); border-radius: 6px; }
.control-row { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--border); }
.inspection-result { margin-top: 10px; padding: 10px; background: #f8fafc; border-radius: 6px; font-size: 13px; }
.inspection-result p { margin: 4px 0; }
@media (max-width: 1100px) { .delegation-grid, .workspace-grid { grid-template-columns: 1fr; } }
</style>
