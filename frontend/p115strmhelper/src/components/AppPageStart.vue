<template>
  <v-container fluid class="app-start pa-4">
    <div class="d-flex align-center flex-wrap gap-2 mb-3">
      <v-icon icon="mdi-view-dashboard-outline" color="primary" size="26" />
      <span class="text-h6 font-weight-medium text-high-emphasis">115 助手仪表盘</span>
    </div>

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" closable class="mb-3"
      @click:close="error = null">
      {{ error }}
    </v-alert>
    <v-alert v-if="actionMessage" :type="actionMessageType" variant="tonal" density="comfortable" closable class="mb-3"
      @click:close="actionMessage = null">
      {{ actionMessage }}
    </v-alert>

    <!-- 分享 STRM 清理（对齐媒体整理：单卡 + 顶栏 + 表格区） -->
    <v-card class="app-start-card mb-4" variant="flat" rounded="0">
      <v-card-item class="py-3">
        <v-card-title class="text-subtitle-1 pa-0 font-weight-medium">
          <v-row align="center" class="ma-n1" no-gutters>
            <v-col cols="12" lg="7" class="pa-1">
              <div class="d-flex align-center flex-wrap gap-2">
                <v-icon icon="mdi-broom" color="primary" size="22" />
                <span class="text-high-emphasis">无效分享 STRM 清理</span>
                <v-chip v-if="deleteMode === 'immediate'" size="small" variant="tonal" color="info" label>
                  立即删除
                </v-chip>
                <v-chip v-else size="small" variant="tonal" color="warning" label>待确认</v-chip>
              </div>
            </v-col>
            <v-col cols="12" lg="5" class="pa-1 d-flex align-center justify-lg-end gap-2 flex-wrap">
              <v-btn-group variant="outlined" divided rounded density="comfortable">
                <v-btn prepend-icon="mdi-refresh" :loading="refreshing" @click="refreshAll">
                  刷新
                </v-btn>
                <v-btn prepend-icon="mdi-play" color="primary" :loading="scanLoading" :disabled="!canScan"
                  @click="runScan">
                  立即扫描
                </v-btn>
              </v-btn-group>
            </v-col>
          </v-row>
        </v-card-title>
      </v-card-item>

      <v-divider />

      <div v-if="deleteMode === 'immediate'" class="pa-4">
        <v-alert v-if="lastSummary" type="info" variant="tonal" density="comfortable" class="text-body-2">
          <div class="mb-1">
            上次扫描：无效 <strong>{{ lastSummary.invalid_strm_count ?? 0 }}</strong> 条 · 已删
            <strong>{{ lastSummary.deleted_count ?? 0 }}</strong> 条
          </div>
          <div v-if="lastSummary.message" class="text-caption text-medium-emphasis">{{ lastSummary.message }}</div>
        </v-alert>
        <div v-else class="text-body-2 text-medium-emphasis">暂无扫描摘要，可在插件全页分享同步中配置清理目录并保存后执行「立即扫描」</div>
      </div>

      <div v-else class="pending-cleanup-plugin-ui">
        <div v-if="pendingLoading" class="pa-4">
          <v-skeleton-loader class="ma-2" type="table" />
        </div>
        <div v-else-if="!pendingBatch" class="text-body-2 text-medium-emphasis py-8 text-center px-4">
          暂无待确认批次，可点击「立即扫描」生成
        </div>
        <template v-else>
          <div
            class="px-4 pt-3 pb-2 d-flex flex-column flex-sm-row flex-wrap align-stretch align-sm-center justify-space-between gap-3">
            <div class="text-body-2 min-w-0">
              待删 <strong>{{ pendingPaths.total }}</strong> 条 STRM
              <span class="text-caption text-medium-emphasis d-block mt-1">
                批次 <code class="text-caption">{{ pendingBatch.request_id }}</code>
                · {{ formatTs(pendingBatch.created_at) }}
              </span>
            </div>
            <div class="d-flex flex-wrap gap-2 flex-shrink-0">
              <v-btn color="error" variant="flat" size="small" prepend-icon="mdi-delete-forever"
                :loading="execId === pendingBatch.request_id" :disabled="!!cancelId"
                @click="executeBatch(pendingBatch.request_id)">
                确认删除
              </v-btn>
              <v-btn variant="outlined" size="small" prepend-icon="mdi-close"
                :loading="cancelId === pendingBatch.request_id" :disabled="!!execId"
                @click="cancelBatch(pendingBatch.request_id)">
                取消
              </v-btn>
            </div>
          </div>
          <v-data-table :headers="batchPathsHeaders" :items="pendingPathsTableItems" :loading="pendingPaths.loading"
            item-value="rowKey" density="compact" hover hide-default-footer fixed-header class="app-start-data-table"
            :height="pendingPathsTableHeight">
            <template #item.path="{ item }">
              <span class="text-body-2 text-break">{{ item.path }}</span>
            </template>
            <template #no-data>
              <div class="text-body-2 text-medium-emphasis py-8 text-center">
                {{ pendingPaths.loading ? '' : '暂无路径或批次已失效，请刷新' }}
              </div>
            </template>
            <template #loading>
              <v-skeleton-loader class="ma-4" type="table" />
            </template>
          </v-data-table>
          <template v-if="!pendingPaths.loading && pendingPaths.total > 0">
            <v-divider />
            <div
              class="app-start-page-footer d-flex flex-column flex-md-row align-stretch align-md-center justify-space-between gap-2 gap-md-3 px-2 px-md-3 py-2">
              <div class="d-flex align-center justify-center justify-md-start gap-2 app-start-page-footer__select">
                <v-select v-model="pendingPaths.limit" :items="missingPageSizes" item-title="title" item-value="value"
                  density="compact" variant="plain" hide-details class="missing-per-page"
                  @update:model-value="onPendingPathsLimitChange" />
              </div>
              <div
                class="text-caption text-medium-emphasis text-center text-md-center flex-grow-1 order-3 order-md-2 px-1 app-start-page-footer__tip">
                {{ pendingPathsPageTip }}
              </div>
              <div class="app-start-pagination-scroll order-2 order-md-3 w-100 w-md-auto">
                <v-pagination v-model="pendingPaths.page" :length="pendingPathsTotalPages"
                  :total-visible="paginationTotalVisible" :show-first-last-page="paginationShowFirstLast"
                  first-aria-label="第一页" last-aria-label="最后一页" prev-aria-label="上一页" next-aria-label="下一页"
                  :density="paginationDensity" size="small" @update:model-value="loadPendingPathsPage" />
              </div>
            </div>
          </template>
        </template>
      </div>
    </v-card>

    <!-- 无效分享 STRM 关联媒体缺失 -->
    <v-card class="app-start-card" variant="flat" rounded="0">
      <v-card-item class="py-3">
        <v-card-title class="text-subtitle-1 pa-0 font-weight-medium">
          <v-row align="center" class="ma-n1" no-gutters>
            <v-col cols="12" md="8" class="pa-1">
              <div class="d-flex align-center gap-2">
                <v-icon icon="mdi-database-off-outline" color="primary" size="22" />
                <span class="text-high-emphasis">无效分享 STRM 关联媒体缺失</span>
              </div>
            </v-col>
            <v-col cols="12" md="4" class="pa-1 d-flex justify-md-end">
              <v-btn v-if="missingTotal > 0" size="small" variant="tonal" color="error" prepend-icon="mdi-delete-sweep"
                :loading="clearAllLoading" @click="clearAllMissing">
                清空全部
              </v-btn>
            </v-col>
          </v-row>
        </v-card-title>
      </v-card-item>

      <v-divider />

      <div class="missing-media-scroll">
        <v-data-table :headers="missingHeaders" :items="missingItems" :loading="missingLoading" item-value="uid"
          density="compact" hover hide-default-footer fixed-header class="app-start-data-table missing-media-table"
          :height="missingTableHeight">
          <template #item.share_code="{ item }">
            <span class="text-caption font-weight-medium text-break d-block">{{ item.share_code || '—' }}</span>
          </template>
          <template #item.receive_code="{ item }">
            <span class="text-caption text-medium-emphasis text-break d-block">{{ item.receive_code || '—' }}</span>
          </template>
          <template #item.strm_path="{ item }">
            <span class="strm-path-cell text-caption text-break d-block">{{ item.strm_path }}</span>
          </template>
          <template #item.type="{ item }">
            <span class="text-caption">{{ item.type || '—' }}</span>
          </template>
          <template #item.title="{ item }">
            <span class="text-caption text-break d-block">{{ item.title || '—' }}</span>
          </template>
          <template #item.year="{ item }">
            <span class="text-caption">{{ item.year != null && item.year !== '' ? item.year : '—' }}</span>
          </template>
          <template #item.season_ep="{ item }">
            <div class="text-caption">
              <template v-if="item.seasons != null && item.seasons !== ''">
                <div>{{ item.seasons }}</div>
              </template>
              <template v-if="item.episodes != null && item.episodes !== ''">
                <div class="text-medium-emphasis">{{ item.episodes }}</div>
              </template>
              <span v-if="seasonEpEmpty(item)">—</span>
            </div>
          </template>
          <template #item.external_ids="{ item }">
            <div class="text-caption missing-id-stack">
              <div v-if="item.tmdbid != null && item.tmdbid !== ''">TMDB: {{ item.tmdbid }}</div>
              <div v-if="item.tvdbid != null && item.tvdbid !== ''">TVDB: {{ item.tvdbid }}</div>
              <div v-if="item.imdbid != null && item.imdbid !== ''">IMDB: {{ item.imdbid }}</div>
              <div v-if="item.doubanid != null && item.doubanid !== ''">豆瓣: {{ item.doubanid }}</div>
              <span v-if="!hasAnyExternalId(item)">—</span>
            </div>
          </template>
          <template #item.history_id="{ item }">
            <span class="text-caption">{{ item.id != null && item.id !== '' ? item.id : '—' }}</span>
          </template>
          <template #item.actions="{ item }">
            <v-btn icon size="small" variant="text" color="error" :loading="deletingUid === item.uid"
              @click="deleteOneMissing(item.uid)">
              <v-icon size="small">mdi-delete-outline</v-icon>
            </v-btn>
          </template>
          <template #no-data>
            <div class="text-body-2 text-medium-emphasis py-8 text-center">暂无记录</div>
          </template>
          <template #loading>
            <v-skeleton-loader class="ma-4" type="table" />
          </template>
        </v-data-table>
      </div>

      <template v-if="!missingLoading && missingTotal > 0">
        <v-divider />
        <div
          class="app-start-page-footer d-flex flex-column flex-md-row align-stretch align-md-center justify-space-between gap-2 gap-md-3 px-2 px-md-3 py-2">
          <div class="d-flex align-center justify-center justify-md-start gap-2 app-start-page-footer__select">
            <v-select v-model="missingLimit" :items="missingPageSizes" item-title="title" item-value="value"
              density="compact" variant="plain" hide-details class="missing-per-page"
              @update:model-value="onMissingLimitChange" />
          </div>
          <div
            class="text-caption text-medium-emphasis text-center text-md-center flex-grow-1 order-3 order-md-2 px-1 app-start-page-footer__tip">
            {{ missingPageTip }}
          </div>
          <div class="app-start-pagination-scroll order-2 order-md-3 w-100 w-md-auto">
            <v-pagination v-model="missingPage" :length="missingTotalPages" :total-visible="paginationTotalVisible"
              :show-first-last-page="paginationShowFirstLast" first-aria-label="第一页" last-aria-label="最后一页"
              prev-aria-label="上一页" next-aria-label="下一页" :density="paginationDensity" size="small"
              @update:model-value="loadMissing" />
          </div>
        </div>
      </template>
    </v-card>

  </v-container>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue';
import { useDisplay } from 'vuetify';
import { P115_STRM_HELPER_PLUGIN_ID } from '../utils/pluginId.js';

const props = defineProps({
  api: {
    type: [Object, Function],
    required: true,
  },
  pluginId: {
    type: String,
    default: P115_STRM_HELPER_PLUGIN_ID,
  },
});

const display = useDisplay();

/** 窄屏少显页码按钮；xs 仅 5 个槽位，sm/md 以下7，md+ 与桌面一致 */
const paginationTotalVisible = computed(() => {
  if (display.mdAndUp.value) return 11;
  if (display.smAndUp.value) return 7;
  return 5;
});

const paginationShowFirstLast = computed(() => display.smAndUp.value);

const paginationDensity = computed(() => (display.xs.value ? 'compact' : 'comfortable'));

const refreshing = ref(false);
const error = ref(null);
const actionMessage = ref(null);
const actionMessageType = ref('info');

const initialConfig = reactive({});
const deleteMode = computed(() =>
  (initialConfig.share_strm_cleanup_config && initialConfig.share_strm_cleanup_config.delete_mode) || 'plugin_ui',
);

const canScan = computed(
  () =>
    !!(initialConfig.enabled && initialConfig.cookies && String(initialConfig.cookies).trim()),
);

const pendingLoading = ref(true);
const shareBatches = ref([]);
const lastSummary = ref(null);

const pendingBatch = computed(() =>
  shareBatches.value.length > 0 ? shareBatches.value[0] : null,
);

const missingLoading = ref(true);
const missingItems = ref([]);
const missingTotal = ref(0);
const missingPage = ref(1);
const missingLimit = ref(25);

const missingPageSizes = [
  { title: '15', value: 15 },
  { title: '25', value: 25 },
  { title: '50', value: 50 },
  { title: '100', value: 100 },
];

const missingHeaders = [
  { title: '分享码', key: 'share_code', sortable: false, minWidth: '96px' },
  { title: '接收码', key: 'receive_code', sortable: false, minWidth: '96px' },
  { title: 'STRM 路径', key: 'strm_path', sortable: false, minWidth: '200px' },
  { title: '类型', key: 'type', sortable: false, width: '88px' },
  { title: '标题', key: 'title', sortable: false, minWidth: '140px' },
  { title: '年份', key: 'year', sortable: false, width: '64px' },
  { title: '季 / 集', key: 'season_ep', sortable: false, minWidth: '88px' },
  { title: '外部 ID', key: 'external_ids', sortable: false, minWidth: '112px' },
  { title: '整理记录', key: 'history_id', sortable: false, width: '80px' },
  { title: '', key: 'actions', sortable: false, align: 'end', width: '52px' },
];

const batchPathsHeaders = [
  { title: 'STRM 路径', key: 'path', sortable: false, minWidth: '320px' },
];

const pendingPaths = reactive({
  requestId: '',
  page: 1,
  limit: 25,
  paths: [],
  total: 0,
  loading: false,
});

const missingTableHeight = computed(() => (missingItems.value.length > 12 ? 520 : 360));

const missingTotalPages = computed(() =>
  Math.max(1, Math.ceil(missingTotal.value / missingLimit.value)),
);

const missingPageTip = computed(() => {
  const t = missingTotal.value;
  if (t <= 0) return '共 0 条';
  const per = missingLimit.value;
  const page = missingPage.value;
  const from = (page - 1) * per + 1;
  const to = Math.min(page * per, t);
  return `第 ${from}–${to} 条，共 ${t} 条`;
});

const pendingPathsPageTip = computed(() => {
  const t = pendingPaths.total;
  const per = pendingPaths.limit;
  const page = pendingPaths.page;
  if (t <= 0) return '共 0 条';
  const from = (page - 1) * per + 1;
  const to = Math.min(page * per, t);
  return `第 ${from}–${to} 条，共 ${t} 条`;
});

const pendingPathsTotalPages = computed(() =>
  Math.max(1, Math.ceil(pendingPaths.total / pendingPaths.limit)),
);

const pendingPathsTableItems = computed(() =>
  pendingPaths.paths.map((path, i) => ({
    rowKey: `${pendingPaths.page}-${i}`,
    path,
  })),
);

const pendingPathsTableHeight = computed(() =>
  pendingPaths.paths.length > 12 ? 420 : 320,
);

const scanLoading = ref(false);
const execId = ref(null);
const cancelId = ref(null);
const deletingUid = ref(null);
const clearAllLoading = ref(false);

const formatTs = (t) => {
  if (t == null) return '';
  const d = typeof t === 'number' ? new Date(t * 1000) : new Date(t);
  return Number.isNaN(d.getTime()) ? String(t) : d.toLocaleString();
};

const hasAnyExternalId = (item) =>
  (item.tmdbid != null && item.tmdbid !== '') ||
  (item.tvdbid != null && item.tvdbid !== '') ||
  (item.imdbid != null && item.imdbid !== '') ||
  (item.doubanid != null && item.doubanid !== '');

const seasonEpEmpty = (item) =>
  (item.seasons == null || item.seasons === '') &&
  (item.episodes == null || item.episodes === '');

const loadConfig = async () => {
  try {
    const configData = await props.api.get(`plugin/${props.pluginId}/get_config`);
    if (configData && typeof configData === 'object') {
      Object.assign(initialConfig, configData);
    }
  } catch (e) {
    console.error(e);
  }
};

const loadPendingPathsPage = async () => {
  if (!pendingPaths.requestId) return;
  pendingPaths.loading = true;
  try {
    const qs = new URLSearchParams({
      request_id: pendingPaths.requestId,
      page: String(pendingPaths.page),
      limit: String(pendingPaths.limit),
    });
    const res = await props.api.get(
      `plugin/${props.pluginId}/share_strm_cleanup_batch_paths?${qs.toString()}`,
    );
    if (res && res.code === 0 && res.data) {
      pendingPaths.paths = Array.isArray(res.data.paths) ? res.data.paths : [];
      pendingPaths.total = Number(res.data.total) || 0;
    } else {
      pendingPaths.paths = [];
      if (res?.msg) {
        error.value = res.msg;
      }
    }
  } catch (e) {
    pendingPaths.paths = [];
    error.value = e.message || '加载路径失败';
  } finally {
    pendingPaths.loading = false;
  }
};

const loadPending = async () => {
  pendingLoading.value = true;
  try {
    const res = await props.api.get(`plugin/${props.pluginId}/share_strm_cleanup_pending`);
    if (res && res.code === 0 && res.data && Array.isArray(res.data.batches)) {
      shareBatches.value = res.data.batches;
    } else {
      shareBatches.value = [];
    }
    if (shareBatches.value.length > 0) {
      const b = shareBatches.value[0];
      pendingPaths.requestId = b.request_id;
      pendingPaths.page = 1;
      pendingPaths.limit = pendingPaths.limit || 25;
      await loadPendingPathsPage();
    } else {
      pendingPaths.requestId = '';
      pendingPaths.paths = [];
      pendingPaths.total = 0;
      pendingPaths.loading = false;
    }
  } catch (e) {
    console.error(e);
    shareBatches.value = [];
    pendingPaths.requestId = '';
    pendingPaths.paths = [];
    pendingPaths.total = 0;
    pendingPaths.loading = false;
  } finally {
    pendingLoading.value = false;
  }
};

const loadSummary = async () => {
  try {
    const res = await props.api.get(`plugin/${props.pluginId}/share_strm_cleanup_last_summary`);
    if (res && res.code === 0 && res.data) {
      lastSummary.value = res.data.summary || null;
    } else {
      lastSummary.value = null;
    }
  } catch (e) {
    lastSummary.value = null;
  }
};

const onPendingPathsLimitChange = () => {
  pendingPaths.page = 1;
  loadPendingPathsPage();
};

const loadMissing = async () => {
  missingLoading.value = true;
  try {
    const qs = new URLSearchParams({
      page: String(missingPage.value),
      limit: String(missingLimit.value),
    });
    const res = await props.api.get(
      `plugin/${props.pluginId}/share_strm_missing_media_list?${qs.toString()}`,
    );
    if (res && res.code === 0 && res.data) {
      missingItems.value = Array.isArray(res.data.items) ? res.data.items : [];
      missingTotal.value = res.data.total || 0;
    } else {
      missingItems.value = [];
      missingTotal.value = 0;
    }
  } catch (e) {
    missingItems.value = [];
    missingTotal.value = 0;
  } finally {
    missingLoading.value = false;
  }
};

const onMissingLimitChange = () => {
  missingPage.value = 1;
  loadMissing();
};

const refreshAll = async () => {
  refreshing.value = true;
  error.value = null;
  try {
    await loadConfig();
    await Promise.all([loadPending(), loadSummary(), loadMissing()]);
    actionMessage.value = '已刷新';
    actionMessageType.value = 'success';
    setTimeout(() => {
      actionMessage.value = null;
    }, 2000);
  } catch (e) {
    error.value = e.message || '刷新失败';
  } finally {
    refreshing.value = false;
  }
};

const runScan = async () => {
  scanLoading.value = true;
  try {
    const res = await props.api.post(`plugin/${props.pluginId}/share_strm_cleanup_scan`);
    if (res && res.code === 0) {
      actionMessage.value = res.msg || '扫描已启动';
      actionMessageType.value = 'success';
      setTimeout(() => {
        loadSummary();
        loadPending();
      }, 1500);
    } else {
      throw new Error(res?.msg || '启动失败');
    }
  } catch (e) {
    error.value = e.message || '启动扫描失败';
  } finally {
    scanLoading.value = false;
  }
};

const executeBatch = async (requestId) => {
  execId.value = requestId;
  try {
    const res = await props.api.post(`plugin/${props.pluginId}/share_strm_cleanup_execute`, {
      request_id: requestId,
    });
    if (res && res.code === 0) {
      actionMessage.value = res.msg || '已执行';
      actionMessageType.value = 'success';
      await loadPending();
    } else {
      throw new Error(res?.msg || '执行失败');
    }
  } catch (e) {
    error.value = e.message || '执行失败';
  } finally {
    execId.value = null;
  }
};

const cancelBatch = async (requestId) => {
  cancelId.value = requestId;
  try {
    const res = await props.api.post(`plugin/${props.pluginId}/share_strm_cleanup_cancel`, {
      request_id: requestId,
    });
    if (res && res.code === 0) {
      actionMessage.value = res.msg || '已取消';
      actionMessageType.value = 'success';
      await loadPending();
    } else {
      throw new Error(res?.msg || '取消失败');
    }
  } catch (e) {
    error.value = e.message || '取消失败';
  } finally {
    cancelId.value = null;
  }
};

const deleteOneMissing = async (uid) => {
  deletingUid.value = uid;
  try {
    const res = await props.api.post(`plugin/${props.pluginId}/share_strm_missing_media_clear`, {
      uid,
      clear_all: false,
    });
    if (res && res.code === 0) {
      await loadMissing();
    } else {
      throw new Error(res?.msg || '删除失败');
    }
  } catch (e) {
    error.value = e.message || '删除失败';
  } finally {
    deletingUid.value = null;
  }
};

const clearAllMissing = async () => {
  if (!confirm('确定清空全部缺失媒体记录？')) return;
  clearAllLoading.value = true;
  try {
    const res = await props.api.post(`plugin/${props.pluginId}/share_strm_missing_media_clear`, {
      clear_all: true,
    });
    if (res && res.code === 0) {
      missingPage.value = 1;
      await loadMissing();
      actionMessage.value = res.msg || '已清空';
      actionMessageType.value = 'success';
    } else {
      throw new Error(res?.msg || '清空失败');
    }
  } catch (e) {
    error.value = e.message || '清空失败';
  } finally {
    clearAllLoading.value = false;
  }
};

onMounted(async () => {
  await loadConfig();
  await Promise.all([loadPending(), loadSummary(), loadMissing()]);
});
</script>

<style scoped>
.app-start {
  max-inline-size: none;
  inline-size: 100%;
}

.app-start-card {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  background: rgb(var(--v-theme-surface));
}

.strm-path-cell {
  max-width: min(48rem, 70vw);
  vertical-align: top;
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: 1.35;
}

.missing-media-scroll {
  overflow-x: auto;
  max-inline-size: 100%;
}

.missing-media-table {
  min-inline-size: 800px;
}

.missing-id-stack {
  line-height: 1.35;
}

.pending-cleanup-plugin-ui {
  min-inline-size: 0;
}

.missing-per-page {
  flex: 0 1 auto;
  inline-size: 8.5rem;
  max-inline-size: 8.5rem;
  min-inline-size: 0;
}

.missing-per-page :deep(.v-field) {
  padding-inline-start: 4px;
}

/* 分页底栏：窄屏不挤出视口，分页可横向滑动 */
.app-start-page-footer {
  min-inline-size: 0;
  box-sizing: border-box;
}

.app-start-page-footer__tip {
  word-break: break-word;
  line-height: 1.35;
}

.app-start-pagination-scroll {
  min-inline-size: 0;
  max-inline-size: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  display: flex;
  justify-content: center;
  padding-block: 2px;
  box-sizing: border-box;
}

.app-start-pagination-scroll :deep(.v-pagination) {
  flex: 0 0 auto;
}

.app-start-pagination-scroll :deep(.v-pagination__list) {
  flex-wrap: nowrap;
}

@media (max-width: 959px) {
  .app-start-page-footer__select {
    inline-size: 100%;
    max-inline-size: 12rem;
    margin-inline: auto;
  }

  .app-start-page-footer__select .missing-per-page {
    flex: 1 1 auto;
    max-inline-size: none;
  }
}

/* 与媒体整理 history 页表格视觉对齐 */
.app-start-data-table :deep(.v-table__wrapper) {
  border-radius: 0;
}

.app-start-data-table :deep(.v-table th) {
  white-space: nowrap;
  font-weight: 600;
  font-size: 0.75rem;
}

.app-start-data-table :deep(.v-data-table__td) {
  vertical-align: top;
}
</style>
