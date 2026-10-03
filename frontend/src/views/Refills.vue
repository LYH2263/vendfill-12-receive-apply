<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'

const orders = ref<any[]>([])
const current = ref<any>(null)
const busy = ref(false)
const error = ref('')

function errText(e: any): string {
  try { return JSON.parse(e.message).detail ?? e.message } catch { return String(e.message || e) }
}
const statusText = (s: string) => s === 'verified' ? '已核销' : s === 'void' ? '已作废' : '未核销'
const badgeClass = (s: string) => s === 'verified' ? 'badge badge-ok' : s === 'void' ? 'badge badge-bad' : 'badge badge-warn'

async function load() {
  orders.value = await api('/refills/orders?location_id=1')
  if (orders.value.length) await selectOrder(orders.value[0].id)
  else current.value = null
}
async function selectOrder(id: number) {
  error.value = ''
  current.value = await api(`/refills/orders/${id}`)
}
async function run() {
  busy.value = true; error.value = ''
  try {
    const o = await api('/refills/run?location_id=1', { method: 'POST' })
    await load()
    await selectOrder(o.id)
  } catch (e: any) { error.value = errText(e) } finally { busy.value = false }
}
async function verify() {
  if (!current.value) return
  busy.value = true; error.value = ''
  try {
    // 一次请求内后端同事务提交货道库存/在途与单据状态，返回实时汇总
    current.value = await api(`/refills/${current.value.id}/verify`, { method: 'POST' })
    await load()
    await selectOrder(current.value.id)
  } catch (e: any) {
    error.value = errText(e)  // 已核销/已作废/货道缺失：三处不动
    await selectOrder(current.value.id)
  } finally { busy.value = false }
}
async function voidOrder() {
  if (!current.value) return
  busy.value = true; error.value = ''
  try {
    await api(`/refills/${current.value.id}/void`, { method: 'POST' })
    await load(); await selectOrder(current.value.id)
  } catch (e: any) { error.value = errText(e) } finally { busy.value = false }
}
onMounted(load)
</script>
<template>
  <h1>补货小票</h1>
  <p class="sub">gap = 容量 − 库存 − 在途 · 到货核销后库存/在途/汇总同一跳变</p>
  <button class="btn" :disabled="busy" @click="run">生成补货单</button>
  <p v-if="error" class="badge badge-bad" style="margin:0.6rem 0 0;padding:0.4rem 0.6rem">{{ error }}</p>

  <div class="vf-machine-layout" style="margin-top:1rem">
    <div class="card" style="margin:0">
      <table>
        <thead><tr><th>#</th><th>补量</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="o in orders" :key="o.id"
              :style="current && o.id === current.id ? 'background:#22303f;cursor:pointer' : 'cursor:pointer'"
              @click="selectOrder(o.id)">
            <td>{{ o.id }}</td><td>{{ o.total_fill }}</td>
            <td><span :class="badgeClass(o.status)">{{ statusText(o.status) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="current">
      <div class="vf-receipt">
        <h2>*** VendFill 补货单 #{{ current.id }} ***</h2>
        <div class="vf-receipt-line" style="font-weight:700;border-bottom:2px dashed #8a7e64">
          <span>货道 / 商品</span><span>补量</span>
        </div>
        <div class="vf-receipt-line" v-for="l in current.lines" :key="l.lane_id">
          <span>{{ l.slot_no }} {{ l.sku_name }}
            <small>({{ l.status === 'need_fill' ? '待补' : l.status === 'full' ? '满仓' : '超占' }})</small>
          </span>
          <span>{{ l.fill_qty }} / 缺{{ l.gap }}</span>
        </div>
        <p style="text-align:center;margin:0.6rem 0 0;font-size:0.72rem;color:#6a5e48">
          <span :class="badgeClass(current.status)">{{ statusText(current.status) }}</span>
        </p>
        <div v-if="current.status === 'verified'" style="font-size:0.72rem;color:#6a5e48;margin-top:0.5rem">
          核销后实时：待补 {{ current.live.need_fill_count }} · 满仓 {{ current.live.full_count }}
          · 建议补量 {{ current.live.total_fill }}
        </div>
        <div style="margin-top:0.8rem;display:flex;gap:0.5rem;justify-content:center">
          <button class="btn" :disabled="busy || current.status !== 'open'" @click="verify">到货核销</button>
          <button class="btn" :disabled="busy || current.status !== 'open'"
                  style="background:var(--vf-amber)" @click="voidOrder">作废</button>
        </div>
      </div>
    </div>
  </div>
</template>
