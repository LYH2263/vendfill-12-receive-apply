<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const data = ref<any>(null)
const orders = ref<any[]>([])
const msg = ref('')
const err = ref('')
const busy = ref(false)

const STATUS_TEXT: Record<string, string> = { pending: '待核销', verified: '已核销', void: '已作废' }
const STATUS_CLASS: Record<string, string> = { pending: 'badge-warn', verified: 'badge-ok', void: 'badge-bad' }

async function loadOrders() {
  orders.value = await api('/refills/orders?location_id=1')
}
async function run() {
  err.value = ''; msg.value = ''
  data.value = await api('/refills/run?location_id=1', { method: 'POST' })
  await loadOrders()
}
async function view(id: number) {
  err.value = ''; msg.value = ''
  data.value = await api(`/refills/${id}`)
}
async function verify() {
  if (!data.value || busy.value) return
  busy.value = true
  err.value = ''; msg.value = ''
  try {
    const r = await api(`/refills/${data.value.id}/verify`, { method: 'POST' })
    data.value.status = r.status
    data.value.verified_at = r.verified_at
    msg.value = `核销成功 #${r.id}：库存/在途已同一跳变 · 待补 ${r.summary.need_fill_count} 道 · 满仓 ${r.summary.full_count} 道 · 新缺口 ${r.summary.total_fill}`
    await loadOrders()
  } catch (e: any) {
    err.value = `核销失败：${e.message}`
  } finally {
    busy.value = false
  }
}
async function voidOrder() {
  if (!data.value || busy.value) return
  busy.value = true
  err.value = ''; msg.value = ''
  try {
    const r = await api(`/refills/${data.value.id}/void`, { method: 'POST' })
    data.value.status = r.status
    msg.value = `补货单 #${r.id} 已作废`
    await loadOrders()
  } catch (e: any) {
    err.value = `作废失败：${e.message}`
  } finally {
    busy.value = false
  }
}
onMounted(async () => {
  try { data.value = await api('/refills/latest?location_id=1') } catch { /* */ }
  await loadOrders()
})
</script>
<template>
  <h1>补货小票</h1>
  <p class="sub">gap = 容量 − 库存 − 在途 · 到货后核销，库存/在途/汇总/满仓同一跳变</p>
  <button class="btn" @click="run">生成补货单</button>
  <div style="margin-top:1rem" v-if="data">
    <div class="vf-receipt">
      <h2>*** VendFill 补货单 #{{ data.id }} ***</h2>
      <p style="text-align:center;margin:0.25rem 0 0.5rem">
        <span class="badge" :class="STATUS_CLASS[data.status]">{{ STATUS_TEXT[data.status] || data.status }}</span>
      </p>
      <div class="vf-receipt-line" style="font-weight:700;border-bottom:2px dashed #8a7e64">
        <span>货道 / 商品</span><span>补量</span>
      </div>
      <div class="vf-receipt-line" v-for="l in data.lines" :key="l.lane_id">
        <span>{{ l.slot_no }} {{ l.sku_name }}
          <small>({{ l.status === 'need_fill' ? '待补' : l.status === 'full' ? '满仓' : '超占' }})</small>
        </span>
        <span>{{ l.fill_qty }} / 缺{{ l.gap }}</span>
      </div>
      <p style="text-align:center;margin:1rem 0 0;font-size:0.72rem;color:#6a5e48">谢谢使用 · 请核对后装机</p>
    </div>
    <div style="margin-top:0.75rem;display:flex;gap:0.5rem">
      <button class="btn" :disabled="busy || data.status !== 'pending'" @click="verify">核销到货</button>
      <button class="btn" :disabled="busy || data.status !== 'pending'" @click="voidOrder">作废</button>
    </div>
    <p v-if="msg" class="badge badge-ok" style="display:inline-block;margin-top:0.75rem">{{ msg }}</p>
    <p v-if="err" class="badge badge-bad" style="display:inline-block;margin-top:0.75rem">{{ err }}</p>
  </div>
  <div class="card" style="margin-top:1.5rem" v-if="orders.length">
    <h2 style="margin-top:0">历史补货单</h2>
    <table>
      <thead><tr><th>单号</th><th>时间</th><th>建议补量</th><th>状态</th><th></th></tr></thead>
      <tbody>
        <tr v-for="o in orders" :key="o.id">
          <td>#{{ o.id }}</td>
          <td>{{ o.created_at?.replace('T', ' ').slice(0, 19) }}</td>
          <td>{{ o.total_fill }}</td>
          <td><span class="badge" :class="STATUS_CLASS[o.status]">{{ STATUS_TEXT[o.status] || o.status }}</span></td>
          <td><button class="btn" @click="view(o.id)">查看</button></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
