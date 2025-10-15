<template>
  <div class="dashboard-widget dashboard-widget--full-sync-mini">
    <v-card :flat="!config?.attrs?.border" class="strm-dash-card full-sync-mini-card h-100 d-flex flex-column">
      <v-card-item v-if="config?.attrs?.title || config?.attrs?.subtitle" class="pb-2">
        <v-card-title class="d-flex flex-wrap align-center gap-2 ps-0 text-subtitle-1">
          <v-icon icon="mdi-sync" color="warning" size="small" />
          {{ config?.attrs?.title || "全量同步" }}
        </v-card-title>
        <v-card-subtitle v-if="config?.attrs?.subtitle">{{ config.attrs.subtitle }}</v-card-subtitle>
        <template v-slot:append>
          <v-btn icon size="small" variant="text" :loading="configLoading" @click="loadConfig">
            <v-icon>mdi-refresh</v-icon>
          </v-btn>
        </template>
      </v-card-item>

      <v-card-text class="pa-3 pt-0 full-sync-mini-card__body flex-grow-1 d-flex flex-column">
        <div v-if="configLoading && !configLoaded" class="text-center py-6">
          <v-progress-circular indeterminate color="primary" size="36" />
        </div>
        <template v-else-if="configLoaded">
          <v-alert v-if="!pluginEnabled" type="warning" variant="tonal" density="compact" class="mb-2">
            请先在插件设置中启用插件
          </v-alert>
          <v-alert v-else-if="!hasCookies" type="warning" variant="tonal" density="compact" class="mb-2">
            请先在插件设置中配置 115 Cookie
          </v-alert>
          <template v-else>
            <v-btn block color="warning" variant="tonal" prepend-icon="mdi-sync" class="mb-2"
              :disabled="!canTriggerFullSync" @click="fullSyncConfirmOpen = true">
              全量同步
            </v-btn>
            <v-btn block color="warning" variant="outlined" prepend-icon="mdi-database-sync"
              :disabled="!canTriggerFullSyncDb" @click="fullSyncDbConfirmOpen = true">
              全量同步数据库
            </v-btn>
            <div v-if="!canTriggerFullSync && canTriggerFullSyncDb" class="text-caption text-medium-emphasis mt-2">
              全量同步需先在配置中填写全量同步路径（每行一条且含 #）
            </div>
          </template>
        </template>
      </v-card-text>

      <v-divider v-if="allowRefresh" />
      <v-card-actions v-if="allowRefresh" class="px-3 py-2 refresh-actions">
        <span class="text-caption text-disabled">{{ lastRefreshedDisplay }}</span>
        <v-spacer />
        <v-btn icon variant="text" size="small" :loading="configLoading" @click="loadConfig">
          <v-icon size="small">mdi-refresh</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>

    <FullSyncConfirmDialog v-model="fullSyncConfirmOpen" :loading="syncLoading"
      :has-media-server-refresh="Boolean(fullSyncMediaServerRefresh)" @confirm="onConfirmFullSync" />

    <v-dialog v-model="fullSyncDbConfirmOpen" max-width="450" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="warning" class="mr-2" />
          确认操作
        </v-card-title>
        <v-card-text>
          您确定要立即执行全量同步数据库吗？该操作会覆盖原有数据库数据
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="grey" variant="text" :disabled="syncDbLoading" @click="fullSyncDbConfirmOpen = false">
            取消
          </v-btn>
          <v-btn color="warning" variant="text" :loading="syncDbLoading" @click="onConfirmFullSyncDb">
            确认执行
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-snackbar v-model="snackbar.show" :color="snackbar.color" timeout="3200">
      {{ snackbar.text }}
    </v-snackbar>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from "vue";
import { P115_STRM_HELPER_PLUGIN_ID } from "../../utils/pluginId.js";
import FullSyncConfirmDialog from "../dialogs/FullSyncConfirmDialog.vue";

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
});

const configLoading = ref(false);
const configLoaded = ref(false);
const lastRefreshedAt = ref(null);

const pluginEnabled = ref(false);
const hasCookies = ref(false);
const fullSyncStrmPaths = ref("");
const fullSyncMediaServerRefresh = ref(false);

const fullSyncConfirmOpen = ref(false);
const fullSyncDbConfirmOpen = ref(false);
const syncLoading = ref(false);
const syncDbLoading = ref(false);

const snackbar = reactive({ show: false, text: "", color: "success" });

function fullSyncPathsCount(raw) {
  if (!raw || typeof raw !== "string") return 0;
  try {
    return raw.split("\n").filter((line) => line.trim() && line.includes("#")).length;
  } catch {
    return 0;
  }
}

const canTriggerFullSyncDb = computed(
  () => pluginEnabled.value && hasCookies.value,
);

const canTriggerFullSync = computed(
  () => canTriggerFullSyncDb.value && fullSyncPathsCount(fullSyncStrmPaths.value) > 0,
);

const lastRefreshedDisplay = computed(() => {
  if (!lastRefreshedAt.value) return "";
  const date = new Date(lastRefreshedAt.value);
  return `更新于: ${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}:${date.getSeconds().toString().padStart(2, "0")}`;
});

async function loadConfig() {
  configLoading.value = true;
  try {
    const data = await props.api.get(`plugin/${P115_STRM_HELPER_PLUGIN_ID}/get_config`);
    if (data) {
      pluginEnabled.value = Boolean(data.enabled);
      hasCookies.value = Boolean(data.cookies && String(data.cookies).trim() !== "");
      fullSyncStrmPaths.value = data.full_sync_strm_paths || "";
      fullSyncMediaServerRefresh.value = Boolean(data.full_sync_media_server_refresh_enabled);
    }
    configLoaded.value = true;
    lastRefreshedAt.value = Date.now();
  } catch (err) {
    console.error("仪表盘获取配置失败:", err);
    configLoaded.value = true;
    snackbar.text = "获取配置失败";
    snackbar.color = "error";
    snackbar.show = true;
  } finally {
    configLoading.value = false;
  }
}

function showSnackbar(text, color = "success") {
  snackbar.text = text;
  snackbar.color = color;
  snackbar.show = true;
}

async function onConfirmFullSync() {
  fullSyncConfirmOpen.value = false;
  if (!canTriggerFullSync.value) {
    showSnackbar("条件不满足，请检查插件状态与全量同步路径", "warning");
    return;
  }
  syncLoading.value = true;
  try {
    const result = await props.api.post(`plugin/${P115_STRM_HELPER_PLUGIN_ID}/full_sync`);
    if (result && result.code === 0) {
      showSnackbar(result.msg || "全量同步任务已启动", "success");
    } else {
      showSnackbar(result?.msg || "启动全量同步失败", "error");
    }
  } catch (err) {
    console.error(err);
    showSnackbar(`启动失败: ${err.message || err}`, "error");
  } finally {
    syncLoading.value = false;
  }
}

async function onConfirmFullSyncDb() {
  fullSyncDbConfirmOpen.value = false;
  if (!canTriggerFullSyncDb.value) {
    showSnackbar("请先启用插件并配置 Cookie", "warning");
    return;
  }
  syncDbLoading.value = true;
  try {
    const result = await props.api.post(`plugin/${P115_STRM_HELPER_PLUGIN_ID}/full_sync_db`);
    if (result && result.code === 0) {
      showSnackbar(result.msg || "全量同步数据库任务已启动", "success");
    } else {
      showSnackbar(result?.msg || "启动失败", "error");
    }
  } catch (err) {
    console.error(err);
    showSnackbar(`启动失败: ${err.message || err}`, "error");
  } finally {
    syncDbLoading.value = false;
  }
}

onMounted(() => {
  loadConfig();
});
</script>
