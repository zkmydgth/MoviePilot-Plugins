<template>
  <DashboardStrmPanel
    v-if="panelKey === 'strm'"
    :api="api"
    :config="config"
    :allow-refresh="allowRefresh"
    :refresh-interval="refreshInterval"
  />
  <DashboardStatusPanel
    v-else-if="panelKey === 'status'"
    :api="api"
    :config="config"
    :allow-refresh="allowRefresh"
    :refresh-interval="refreshInterval"
  />
  <DashboardSyncDelPanel
    v-else-if="panelKey === 'sync_del'"
    :api="api"
    :config="config"
    :allow-refresh="allowRefresh"
    :refresh-interval="refreshInterval"
  />
  <DashboardManualTransferPanel
    v-else-if="panelKey === 'manual_transfer'"
    :api="api"
    :config="config"
    :allow-refresh="allowRefresh"
  />
</template>

<script setup>
import { computed, onMounted } from "vue";
import { ensureSentryInitialized } from "../utils/init-sentry.js";
import DashboardStrmPanel from "./dashboard/DashboardStrmPanel.vue";
import DashboardStatusPanel from "./dashboard/DashboardStatusPanel.vue";
import DashboardSyncDelPanel from "./dashboard/DashboardSyncDelPanel.vue";
import DashboardManualTransferPanel from "./dashboard/DashboardManualTransferPanel.vue";

const props = defineProps({
  api: {
    type: [Object, Function],
    required: true,
  },
  config: {
    type: Object,
    default: () => ({ attrs: {} }),
  },
  allowRefresh: {
    type: Boolean,
    default: false,
  },
  refreshInterval: {
    type: Number,
    default: 0,
  },
});

/** 与后端 get_dashboard_meta 的 key 一致：strm | status | sync_del | manual_transfer */
const panelKey = computed(() => {
  const k = (props.config?.key ?? "").trim();
  if (k === "status") return "status";
  if (k === "sync_del") return "sync_del";
  if (k === "manual_transfer") return "manual_transfer";
  return "strm";
});

onMounted(() => {
  ensureSentryInitialized();
});
</script>

<style>
@import "./dashboard/dashboard-panels.css";
</style>
