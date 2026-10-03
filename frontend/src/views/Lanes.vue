<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const rows = ref<any[]>([])
const orders = ref<any[]>([])
const detail = ref<any>(null)
const busy = ref(false)
const error = ref('')

function errText(e: any): string {
  try { return JSON.parse(e.message).detail ?? e.message } catch { return String(e.message || e) }
}
const statusText = (s: string) => s === 'verified' ? '已核销' : s === 'void' ? '已作废' : '未核销'

async function loadLanes() { rows.value = await api('/lanes') }
async function loadOrders() {
  orders.value = await api('/refills/orders?location_id=1')
  detail.value = null
  if (orders.value.length) detail.value = await api(`/refills/orders/${orders.value[0].id}`)
}
async function verifyLatest() {
  if (!detail.value) return
  busy.value = true; error.value = ''
  try {
    // 核销同事务提交；成功后货道数字与右侧小票立刻按新库存/在途一起跳
    await api(`/refills/${detail.value.id}/verify`, { method: 'POST' })
  } catch (e: any) {
    error.value = errText(e)
  } finally {
    busy.value = false
    await Promise.all([loadLanes(), loadOrders()])
  }
}
onMounted(async () => { await Promise.all([loadLanes(), loadOrders()]) })
</script>
<template>
  <h1>货道格子</h1>
  <p class="sub">机面货道网格 · 格内库存与在途 · 右侧最新补货单（核销三处同一跳变）</p>
  <p v-if="error" class="badge badge-bad" style="margin:0 0 0.6rem;padding:0.4rem 0.6rem">{{ error }}</p>
  <div class="vf-machine-layout">
    <div class="vf-slot-grid">
      <div v-for="r in rows" :key="r.id" class="vf-slot">
        <div class="vf-slot-no">{{ r.slot_no }}</div>
        <div class="vf-slot-sku">{{ r.sku_name }}</div>
        <div class="vf-slot-bar">
          <div
            class="vf-slot-fill"
            :class="{ 'vf-need': r.gap > 0 }"
            :style="{ width: Math.min(r.fill_pct, 100) + '%' }"
          />
        </div>
        <div class="vf-slot-meta">
          库{{ r.stock }}/{{ r.capacity }} · 途{{ r.in_transit }} · 缺 {{ r.gap }}
        </div>
      </div>
    </div>
    <aside>
      <div class="vf-receipt" v-if="detail">
        <h2>*** 补货单 #{{ detail.id }} ***</h2>
        <div class="vf-receipt-line" v-for="l in detail.lines" :key="l.lane_id">
          <span>{{ l.slot_no }} {{ l.sku_name }}</span>
          <span>x{{ l.fill_qty }}</span>
        </div>
        <p class="muted" style="margin:0.75rem 0 0.3rem;font-size:0.72rem;color:#6a5e48;text-align:center">
          状态：{{ statusText(detail.status) }}
        </p>
        <div v-if="detail.status === 'verified'"
             style="font-size:0.72rem;color:#6a5e48;text-align:center">
          核销后实时：待补 {{ detail.live.need_fill_count }} · 满仓 {{ detail.live.full_count }}
          · 补量 {{ detail.live.total_fill }}
        </div>
        <div style="text-align:center;margin-top:0.6rem">
          <button class="btn" :disabled="busy || detail.status !== 'open'" @click="verifyLatest">
            到货核销
          </button>
        </div>
      </div>
      <p v-else class="muted">暂无补货单，请先到补货小票页生成。</p>
    </aside>
  </div>
</template>
