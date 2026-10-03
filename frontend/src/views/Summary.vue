<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../api'
const s = ref<any>({})
async function load() { s.value = await api('/refills/summary?location_id=1') }
const statusText = (x?: string) => x === 'verified' ? '已核销' : x === 'void' ? '已作废' : '未核销'
onMounted(load)
</script>
<template>
  <h1>汇总</h1>
  <p class="sub">
    本点位实时补货合计（按当前库存/在途重算）· 补货单 #{{ s.order_id }}：{{ statusText(s.status) }}
    <button class="btn" style="margin-left:0.6rem;padding:0.2rem 0.6rem" @click="load">刷新</button>
  </p>
  <div class="card grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:1rem">
    <div><div class="muted">建议补货总量</div><div class="stat">{{ s.total_fill }}</div></div>
    <div><div class="muted">待补货道</div><div class="stat">{{ s.need_fill_count }}</div></div>
    <div><div class="muted">满仓货道</div><div class="stat">{{ s.full_count }}</div></div>
    <div><div class="muted">超占货道</div><div class="stat">{{ s.overbooked_count }}</div></div>
  </div>
</template>
