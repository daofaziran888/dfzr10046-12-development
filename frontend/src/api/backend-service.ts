import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export class DelegationApiError extends Error {
  status: number
  data: Record<string, unknown>

  constructor(message: string, status: number, data: Record<string, unknown>) {
    super(message)
    this.name = 'DelegationApiError'
    this.status = status
    this.data = data
  }
}

async function delegationRequest<T = Record<string, unknown>>(
  path: string,
  operator: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await request(path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Operator': operator,
      ...(options.headers ?? {}),
    },
  })
  const data = (await response.json().catch(() => ({}))) as Record<string, unknown>
  if (!response.ok || data.ok === false) {
    throw new DelegationApiError(
      String(data.message ?? `接口返回 ${response.status}`),
      response.status,
      data,
    )
  }
  return data as T
}

export const delegationApi = {
  list(operator: string) {
    return delegationRequest<{ items: any[]; total: number }>(
      `/api/surrogate_key/delegations?operator=${encodeURIComponent(operator)}`,
      operator,
    )
  },
  create(operator: string, payload: Record<string, unknown>) {
    return delegationRequest('/api/surrogate_key/delegations', operator, {
      method: 'POST',
      body: JSON.stringify({ ...payload, operator }),
    })
  },
  revoke(operator: string, id: number | string, version?: number) {
    return delegationRequest(`/api/surrogate_key/delegations/${id}/revoke`, operator, {
      method: 'POST',
      body: JSON.stringify({ operator, version }),
    })
  },
  saveDraft(operator: string, id: number | string, payload: Record<string, unknown>) {
    return delegationRequest(`/api/surrogate_key/delegations/${id}/draft`, operator, {
      method: 'POST',
      body: JSON.stringify({ ...payload, operator }),
    })
  },
  getDraft(operator: string, id: number | string, number: number | string) {
    return delegationRequest<{ draft: any }>(
      `/api/surrogate_key/delegations/${id}/draft/${number}?operator=${encodeURIComponent(operator)}`,
      operator,
    )
  },
  confirm(operator: string, id: number | string, payload: Record<string, unknown>) {
    return delegationRequest(`/api/surrogate_key/delegations/${id}/confirm`, operator, {
      method: 'POST',
      body: JSON.stringify({ ...payload, operator }),
    })
  },
  todos(operator: string) {
    return delegationRequest<{ items: any[]; total: number }>(
      `/api/surrogate_key/todos?operator=${encodeURIComponent(operator)}`,
      operator,
    )
  },
  usage(operator: string, dimension?: string) {
    const query = new URLSearchParams({ operator })
    if (dimension) query.set('dimension', dimension)
    const suffix = query.toString() ? `?${query.toString()}` : ''
    return delegationRequest<{ items: any[]; total: number }>(
      `/api/surrogate_key/usage${suffix}`,
      operator,
    )
  },
  inspect(operator: string, dimension: string, number: number | string, delegationId?: number | string) {
    const query = new URLSearchParams({ dimension, number: String(number), operator })
    if (delegationId) query.set('delegation_id', String(delegationId))
    return delegationRequest(`/api/surrogate_key/numbers/${number}?${query.toString()}`, operator)
  },
  crossAttempt(operator: string, id: number | string, payload: Record<string, unknown>) {
    return delegationRequest(`/api/surrogate_key/delegations/${id}/confirm`, operator, {
      method: 'POST',
      body: JSON.stringify({ ...payload, operator }),
    })
  },
  setupScenario(operator: string) {
    return delegationRequest('/api/surrogate_key/scenarios/delegation-conflict', operator, {
      method: 'POST',
      body: JSON.stringify({ operator }),
    })
  },
  race(operator: string, id: number | string) {
    return delegationRequest(`/api/surrogate_key/delegations/${id}/race-conflict`, operator, {
      method: 'POST',
      body: JSON.stringify({ operator, settle: true }),
    })
  },
}
