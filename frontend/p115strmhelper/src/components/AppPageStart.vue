<template>
  <div class="plugin-app-page-content d-flex flex-column gap-4">
    <div class="d-flex align-center flex-wrap gap-2">
      <div class="d-flex align-center gap-2 min-w-0">
        <v-icon icon="mdi-view-dashboard-outline" color="primary" size="28" />
        <h1 class="text-h6 font-weight-medium text-high-emphasis text-truncate">115助手仪表盘</h1>
      </div>
      <v-spacer />
      <v-btn color="primary" variant="tonal" size="small" prepend-icon="mdi-refresh" :loading="refreshing"
        @click="refreshAll">
        刷新
      </v-btn>
    </div>

    <v-alert v-if="error" type="error" variant="tonal" density="comfortable" closable>{{ error }}</v-alert>
    <v-alert v-if="actionMessage" :type="actionMessageType" variant="tonal" density="comfortable" closable>
      {{ actionMessage }}
    </v-alert>

    <v-skeleton-loader v-if="loading && !initialDataLoaded" type="card, card, article" />

    <template v-else>
      <!-- 优先：系统状态 + 账户信息 -->
      <v-row class="dashboard-status-row" align="stretch">
        <v-col cols="12" md="6" class="d-flex flex-column mb-4 mb-md-0">
          <v-card class="dashboard-status-card w-100 d-flex flex-column flex-grow-1">
            <v-card-item>
              <v-card-title class="text-subtitle-1 d-flex align-center">
                <v-icon icon="mdi-information" class="mr-2" size="small" />
                系统状态
              </v-card-title>
            </v-card-item>
            <v-divider />
            <v-card-text class="pa-0 flex-grow-1 d-flex flex-column">
              <v-list class="bg-transparent pa-0">
                <v-list-item class="px-4 py-1" style="min-height: 40px;">
                  <template v-slot:prepend>
                    <v-icon :color="status.enabled ? 'success' : 'grey'" icon="mdi-power" size="small" />
                  </template>
                  <v-list-item-title class="text-body-2">插件状态</v-list-item-title>
                  <template v-slot:append>
                    <v-chip :color="status.enabled ? 'success' : 'grey'" size="small" variant="tonal">
                      {{ status.enabled ? '已启用' : '已禁用' }}
                    </v-chip>
                  </template>
                </v-list-item>
                <v-divider class="my-0" />
                <v-list-item class="px-4 py-1" style="min-height: 40px;">
                  <template v-slot:prepend>
                    <v-icon :color="status.has_client && initialConfig?.cookies ? 'success' : 'error'"
                      icon="mdi-account-check" size="small" />
                  </template>
                  <v-list-item-title class="text-body-2">115客户端状态</v-list-item-title>
                  <template v-slot:append>
                    <v-chip :color="status.has_client && initialConfig?.cookies ? 'success' : 'error'" size="small"
                      variant="tonal">
                      {{ status.has_client && initialConfig?.cookies ? '已连接' : '未连接' }}
                    </v-chip>
                  </template>
                </v-list-item>
                <v-divider class="my-0" />
                <v-list-item class="px-4 py-1" style="min-height: 40px;">
                  <template v-slot:prepend>
                    <v-icon :color="status.running ? 'warning' : 'success'" icon="mdi-play-circle" size="small" />
                  </template>
                  <v-list-item-title class="text-body-2">任务状态</v-list-item-title>
                  <template v-slot:append>
                    <v-chip :color="status.running ? 'warning' : 'success'" size="small" variant="tonal">
                      {{ status.running ? '运行中' : '空闲' }}
                    </v-chip>
                  </template>
                </v-list-item>
              </v-list>
            </v-card-text>
          </v-card>
        </v-col>

        <v-col cols="12" md="6" class="d-flex flex-column mb-4 mb-md-0">
          <v-card class="dashboard-status-card w-100 d-flex flex-column flex-grow-1">
            <v-card-item>
              <v-card-title class="text-subtitle-1 d-flex align-center">
                <v-icon icon="mdi-account-box" class="mr-2" size="small" />
                115账户信息
              </v-card-title>
            </v-card-item>
            <v-divider />
            <v-card-text class="pa-0 flex-grow-1 d-flex flex-column">
              <v-skeleton-loader v-if="userInfo.loading || storageInfo.loading"
                type="list-item-avatar-three-line, list-item-three-line" />
              <div v-else>
                <v-alert v-if="userInfo.error || storageInfo.error" type="warning" variant="tonal" density="comfortable"
                  class="ma-4">
                  {{ userInfo.error || storageInfo.error }}
                </v-alert>
                <v-list v-else class="bg-transparent pa-0">
                  <v-list-item class="px-4 py-2">
                    <template v-slot:prepend>
                      <v-avatar size="36" class="mr-2">
                        <v-img v-if="userInfo.avatar" :src="userInfo.avatar" :alt="userInfo.name" />
                        <v-icon v-else icon="mdi-account-circle" />
                      </v-avatar>
                    </template>
                    <v-list-item-title class="text-body-1 font-weight-medium">
                      {{ userInfo.name || '未知用户' }}
                    </v-list-item-title>
                  </v-list-item>
                  <v-divider class="my-0" />
                  <v-list-item class="px-4 py-2">
                    <template v-slot:prepend>
                      <v-icon :color="userInfo.is_vip ? 'amber-darken-2' : 'grey'" icon="mdi-shield-crown"
                        size="small" />
                    </template>
                    <v-list-item-title class="text-body-2">VIP状态</v-list-item-title>
                    <template v-slot:append>
                      <v-chip :color="userInfo.is_vip ? 'success' : 'grey'" size="small" variant="tonal">
                        {{
                          userInfo.is_vip
                            ? userInfo.is_forever_vip
                              ? '永久VIP'
                              : `VIP (至 ${userInfo.vip_expire_date || 'N/A'})`
                            : '非VIP'
                        }}
                      </v-chip>
                    </template>
                  </v-list-item>
                  <v-divider class="my-0" />
                  <v-list-item class="px-4 py-3">
                    <v-list-item-title class="text-body-2 mb-1">存储空间</v-list-item-title>
                    <v-list-item-subtitle v-if="storageInfo.used && storageInfo.total" class="text-caption">
                      已用 {{ storageInfo.used }} / 总共 {{ storageInfo.total }} (剩余 {{ storageInfo.remaining }})
                    </v-list-item-subtitle>
                    <v-progress-linear v-if="storageInfo.used && storageInfo.total"
                      :model-value="calculateStoragePercentage(storageInfo.used, storageInfo.total)" color="primary"
                      height="8" rounded class="mt-2" />
                    <v-list-item-subtitle v-else class="text-caption text-medium-emphasis">
                      存储信息不可用
                    </v-list-item-subtitle>
                  </v-list-item>
                </v-list>
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>

      <!-- 下方：STRM 同步执行记录 -->
      <v-row class="mt-0 mt-md-2">
        <v-col cols="12">
          <v-card class="strm-history-card" variant="flat">
            <v-card-item class="strm-history-card__header py-3">
              <v-card-title class="text-subtitle-1 d-flex align-center flex-wrap ps-0">
                <v-icon icon="mdi-sync" class="mr-2" color="primary" size="small" />
                STRM 同步状态
                <v-chip class="ms-2" size="x-small" variant="tonal" color="primary">{{ strmHistoryTotal }} 条</v-chip>
              </v-card-title>
              <template v-slot:append>
                <div class="d-flex align-center flex-shrink-0">
                  <v-btn v-if="strmHistoryTotal > 0" size="small" variant="text" color="error" class="mr-1"
                    prepend-icon="mdi-delete-sweep" :loading="deletingAllStrm" @click="confirmDeleteAllStrm">
                    清空
                  </v-btn>
                  <v-btn icon size="small" variant="text" :loading="strmHistoryLoading" @click="loadStrmHistory">
                    <v-icon>mdi-refresh</v-icon>
                  </v-btn>
                </div>
              </template>
            </v-card-item>
            <v-card-text class="strm-history-card__body">
              <div class="strm-filter-row d-flex flex-wrap align-center gap-2 mb-4">
                <v-select v-model="strmKindSelected" :items="strmKindSelectItems" item-title="title" item-value="value"
                  label="任务类型" density="compact" hide-details clearable variant="outlined" class="strm-kind-field"
                  @update:model-value="applyStrmFilter" />
              </div>
              <v-skeleton-loader v-if="strmHistoryLoading" type="card, card, card" />
              <div v-else-if="strmHistoryItems.length === 0" class="text-center py-10">
                <v-icon icon="mdi-playlist-remove" size="56" color="grey" class="mb-3 opacity-40" />
                <div class="text-body-1 text-medium-emphasis">暂无执行记录</div>
                <div class="text-caption text-medium-emphasis mt-1">完成一次全量、增量或分享同步后将在此展示</div>
              </div>
              <div v-else class="strm-history-list">
                <v-card v-for="(row, idx) in strmHistoryItems" :key="row.unique || idx" variant="outlined"
                  class="strm-history-item mb-3" :class="{ 'strm-history-item--fail': !row.success }">
                  <v-card-item class="pb-2">
                    <div class="flex-grow-1 min-w-0">
                      <div class="d-flex flex-wrap align-center gap-2 mb-2">
                        <v-chip size="small" color="primary" variant="flat">{{ kindLabel(row.kind) }}</v-chip>
                        <v-chip size="small" :color="row.success ? 'success' : 'error'" variant="tonal">
                          {{ row.success ? '成功' : '失败' }}
                        </v-chip>
                      </div>
                      <div class="text-h6 font-weight-medium text-high-emphasis">
                        {{ row.finished_at }}
                      </div>
                      <div class="text-body-2 text-medium-emphasis mt-2">
                        耗时 <strong class="text-high-emphasis">{{ formatNum(row.elapsed_sec) }}</strong> 秒 ·
                        扫描 <strong class="text-high-emphasis">{{ row.total_iterated }}</strong> 项
                        <template v-if="row.kind === 'increment'">
                          · API <strong class="text-high-emphasis">{{ row.api_requests }}</strong> 次
                        </template>
                      </div>
                    </div>
                    <template v-slot:append>
                      <v-btn icon size="small" variant="text" color="error" :loading="deletingStrmId === row.unique"
                        :disabled="!row.unique" @click="confirmDeleteStrm(row)">
                        <v-icon size="small">mdi-delete-outline</v-icon>
                      </v-btn>
                    </template>
                  </v-card-item>
                  <v-card-text class="pt-0">
                    <div class="text-caption text-medium-emphasis strm-section-label mb-2">生成统计</div>
                    <div class="d-flex flex-wrap gap-1 mb-1">
                      <v-chip v-for="s in parseStatsEntries(row.stats, row.kind)" :key="s.key" size="small"
                        :color="statChipColor(s)" variant="tonal">
                        {{ s.label }} {{ formatStatValue(s.value) }}
                      </v-chip>
                    </div>
                    <div v-if="parseExtraEntries(row.extra).length" class="mt-3">
                      <div class="text-caption text-medium-emphasis strm-section-label mb-2">补充信息</div>
                      <div v-for="(ex, ei) in parseExtraEntries(row.extra)" :key="ei"
                        class="text-body-2 strm-extra-line">
                        <span class="text-medium-emphasis">{{ ex.label }}</span>
                        <a v-if="ex.href" :href="ex.href" target="_blank" rel="noopener noreferrer"
                          class="text-primary ms-1">{{ ex.display }}</a>
                        <span v-else class="ms-1 text-high-emphasis" :title="ex.full">{{ ex.display }}</span>
                      </div>
                    </div>
                    <v-alert v-if="!row.success && row.error" type="error" variant="tonal" density="compact"
                      class="mt-3" border="start">
                      {{ row.error }}
                    </v-alert>
                  </v-card-text>
                </v-card>
              </div>
              <div v-if="strmHistoryTotal > 0" class="d-flex flex-wrap align-center justify-space-between mt-2 gap-2">
                <div class="text-caption text-medium-emphasis">
                  第 {{ (strmHistoryPage - 1) * strmHistoryLimit + 1 }} –
                  {{ Math.min(strmHistoryPage * strmHistoryLimit, strmHistoryTotal) }} 条，共 {{ strmHistoryTotal }} 条
                </div>
                <v-pagination v-model="strmHistoryPage" :length="Math.ceil(strmHistoryTotal / strmHistoryLimit)"
                  :total-visible="7" density="comfortable" @update:model-value="loadStrmHistory" />
              </div>
            </v-card-text>
          </v-card>
        </v-col>
      </v-row>
    </template>

    <v-dialog v-model="deleteStrmConfirm.show" max-width="420" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="error" class="mr-2" />
          删除记录
        </v-card-title>
        <v-card-text>确定删除这条 STRM 执行历史吗？</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="grey" variant="text" :disabled="deletingStrmId" @click="deleteStrmConfirm.show = false">
            取消
          </v-btn>
          <v-btn color="error" variant="text" :loading="deletingStrmId" @click="handleConfirmDeleteStrm">
            删除
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="deleteAllStrmConfirm" max-width="450" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="error" class="mr-2" />
          清空历史
        </v-card-title>
        <v-card-text>
          <div class="mb-2">确定清空全部 <strong>{{ strmHistoryTotal }}</strong> 条 STRM 执行历史吗？</div>
          <v-alert type="error" variant="tonal" density="compact" class="mt-2" icon="mdi-alert">
            <div class="text-caption">此操作不可恢复</div>
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="grey" variant="text" :disabled="deletingAllStrm" @click="deleteAllStrmConfirm = false">
            取消
          </v-btn>
          <v-btn color="error" variant="text" :loading="deletingAllStrm" @click="handleConfirmDeleteAllStrm">
            确认清空
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue';
import {
  KIND_LABELS,
  kindLabel,
  parseStatsEntries,
  parseExtraEntries,
  statChipColor,
  formatStatValue,
  formatNum,
} from '../utils/strmHistoryDisplay.js';
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
  navKey: {
    type: String,
    default: '',
  },
});

const loading = ref(true);
const refreshing = ref(false);
const initialDataLoaded = ref(false);
const error = ref(null);
const actionMessage = ref(null);
const actionMessageType = ref('info');

const initialConfig = reactive({});

const status = reactive({
  enabled: false,
  has_client: false,
  running: false,
});

const userInfo = reactive({
  name: null,
  is_vip: null,
  is_forever_vip: null,
  vip_expire_date: null,
  avatar: null,
  error: null,
  loading: true,
});

const storageInfo = reactive({
  total: null,
  used: null,
  remaining: null,
  error: null,
  loading: true,
});

/** 任务类型筛选：与后端 kind 一致，空字符串表示不筛选 */
const strmKindSelected = ref('');
const strmKindApplied = ref('');
const strmHistoryPage = ref(1);
const strmHistoryLimit = ref(20);
const strmHistoryTotal = ref(0);
const strmHistoryItems = ref([]);
const strmHistoryLoading = ref(false);
const deletingStrmId = ref(null);
const deletingAllStrm = ref(false);
const deleteStrmConfirm = reactive({ show: false, row: null });
const deleteAllStrmConfirm = ref(false);

const calculateStoragePercentage = (used, total) => {
  if (!used || !total) return 0;
  const parseSize = (sizeStr) => {
    if (!sizeStr || typeof sizeStr !== 'string') return 0;
    const value = parseFloat(sizeStr);
    if (isNaN(value)) return 0;
    if (sizeStr.toUpperCase().includes('TB')) return value * 1024 * 1024;
    if (sizeStr.toUpperCase().includes('GB')) return value * 1024;
    if (sizeStr.toUpperCase().includes('MB')) return value;
    return value;
  };
  const usedValue = parseSize(used);
  const totalValue = parseSize(total);
  if (totalValue === 0) return 0;
  return Math.min(Math.max((usedValue / totalValue) * 100, 0), 100);
};

const strmKindSelectItems = [
  { title: '全部类型', value: '' },
  ...Object.entries(KIND_LABELS).map(([value, title]) => ({ title, value })),
];

const getStatus = async () => {
  loading.value = true;
  error.value = null;
  try {
    const result = await props.api.get(`plugin/${props.pluginId}/get_status`);
    if (result && result.code === 0 && result.data) {
      status.enabled = Boolean(result.data.enabled);
      status.has_client = Boolean(result.data.has_client);
      status.running = Boolean(result.data.running);
      try {
        const configData = await props.api.get(`plugin/${props.pluginId}/get_config`);
        if (configData) {
          Object.assign(initialConfig, configData);
        }
      } catch (configErr) {
        console.error('获取配置失败:', configErr);
      }
      initialDataLoaded.value = true;
    } else {
      status.enabled = Boolean(initialConfig.enabled);
      status.has_client = Boolean(initialConfig.cookies && initialConfig.cookies.trim() !== '');
      status.running = false;
      initialDataLoaded.value = true;
      try {
        const configData = await props.api.get(`plugin/${props.pluginId}/get_config`);
        if (configData) {
          Object.assign(initialConfig, configData);
        }
      } catch (configErr) {
        console.error('获取配置失败:', configErr);
      }
      throw new Error(result?.msg || '获取状态失败');
    }
  } catch (err) {
    error.value = `获取状态失败: ${err.message || '未知错误'}`;
    console.error('获取状态失败:', err);
    initialDataLoaded.value = true;
  } finally {
    loading.value = false;
  }
};

async function fetchUserStorageStatus() {
  userInfo.loading = true;
  userInfo.error = null;
  storageInfo.loading = true;
  storageInfo.error = null;
  try {
    const response = await props.api.get(`plugin/${props.pluginId}/user_storage_status`);
    if (response && response.success) {
      if (response.user_info) {
        Object.assign(userInfo, response.user_info);
      } else {
        userInfo.error = '未能获取有效的用户信息。';
      }
      if (response.storage_info) {
        Object.assign(storageInfo, response.storage_info);
      } else {
        storageInfo.error = '未能获取有效的存储空间信息。';
      }
    } else {
      const errMsg = response?.error_message || '获取用户和存储信息失败。';
      userInfo.error = errMsg;
      storageInfo.error = errMsg;
      if (errMsg.includes('Cookie') || errMsg.includes('未配置')) {
        status.has_client = false;
      }
    }
  } catch (err) {
    console.error('获取用户/存储状态失败:', err);
    const msg = `请求用户/存储状态时出错: ${err.message || '未知网络错误'}`;
    userInfo.error = msg;
    storageInfo.error = msg;
  } finally {
    userInfo.loading = false;
    storageInfo.loading = false;
  }
}

const loadStrmHistory = async () => {
  strmHistoryLoading.value = true;
  try {
    const qs = new URLSearchParams({
      page: String(strmHistoryPage.value),
      limit: String(strmHistoryLimit.value),
    });
    const k = strmKindApplied.value?.trim();
    if (k) {
      qs.set('kind', k);
    }
    const response = await props.api.get(
      `plugin/${props.pluginId}/get_strm_sync_history?${qs.toString()}`,
    );
    if (response && response.code === 0 && response.data) {
      strmHistoryItems.value = Array.isArray(response.data.items) ? response.data.items : [];
      strmHistoryTotal.value = response.data.total || 0;
    } else {
      strmHistoryItems.value = [];
      strmHistoryTotal.value = 0;
    }
  } catch (err) {
    console.error('加载 STRM 执行历史失败:', err);
    strmHistoryItems.value = [];
    strmHistoryTotal.value = 0;
    actionMessage.value = `加载 STRM 历史失败: ${err.message || '未知错误'}`;
    actionMessageType.value = 'error';
  } finally {
    strmHistoryLoading.value = false;
  }
};

const applyStrmFilter = () => {
  const v = strmKindSelected.value;
  strmKindApplied.value = v != null && v !== '' ? String(v).trim() : '';
  strmHistoryPage.value = 1;
  loadStrmHistory();
};

const confirmDeleteStrm = (row) => {
  if (!row?.unique) return;
  deleteStrmConfirm.row = row;
  deleteStrmConfirm.show = true;
};

const handleConfirmDeleteStrm = async () => {
  const unique = deleteStrmConfirm.row?.unique;
  if (!unique) return;
  deleteStrmConfirm.show = false;
  deletingStrmId.value = unique;
  try {
    const response = await props.api.post(
      `plugin/${props.pluginId}/delete_strm_sync_history`,
      { key: unique },
    );
    if (response && response.code === 0) {
      actionMessage.value = response.msg || '删除成功';
      actionMessageType.value = 'success';
      if (strmHistoryItems.value.length === 1 && strmHistoryPage.value > 1) {
        strmHistoryPage.value--;
      }
      await loadStrmHistory();
    } else {
      actionMessage.value = response?.msg || '删除失败';
      actionMessageType.value = 'error';
    }
  } catch (err) {
    console.error('删除 STRM 历史失败:', err);
    actionMessage.value = `删除失败: ${err.message || '未知错误'}`;
    actionMessageType.value = 'error';
  } finally {
    deletingStrmId.value = null;
    deleteStrmConfirm.row = null;
  }
};

const confirmDeleteAllStrm = () => {
  if (strmHistoryTotal.value === 0) return;
  deleteAllStrmConfirm.value = true;
};

const handleConfirmDeleteAllStrm = async () => {
  deleteAllStrmConfirm.value = false;
  deletingAllStrm.value = true;
  try {
    const response = await props.api.post(`plugin/${props.pluginId}/delete_all_strm_sync_history`);
    if (response && response.code === 0) {
      actionMessage.value = response.msg || '已清空';
      actionMessageType.value = 'success';
      strmHistoryPage.value = 1;
      await loadStrmHistory();
    } else {
      actionMessage.value = response?.msg || '清空失败';
      actionMessageType.value = 'error';
    }
  } catch (err) {
    console.error('清空 STRM 历史失败:', err);
    actionMessage.value = `清空失败: ${err.message || '未知错误'}`;
    actionMessageType.value = 'error';
  } finally {
    deletingAllStrm.value = false;
  }
};

const refreshAll = async () => {
  refreshing.value = true;
  error.value = null;
  await getStatus();
  if (status.has_client && initialConfig?.cookies) {
    await fetchUserStorageStatus();
  } else {
    userInfo.loading = false;
    storageInfo.loading = false;
    if (!initialConfig?.cookies) {
      userInfo.error = '请先配置115 Cookie。';
      storageInfo.error = '请先配置115 Cookie。';
    } else if (!status.has_client) {
      userInfo.error = '115客户端未连接或Cookie无效。';
      storageInfo.error = '115客户端未连接或Cookie无效。';
    }
  }
  await loadStrmHistory();
  refreshing.value = false;
  actionMessage.value = '已刷新';
  actionMessageType.value = 'success';
  setTimeout(() => {
    actionMessage.value = null;
  }, 2500);
};

onMounted(async () => {
  await getStatus();
  if (status.has_client && initialConfig?.cookies) {
    await fetchUserStorageStatus();
  } else {
    userInfo.loading = false;
    storageInfo.loading = false;
    if (!initialConfig?.cookies) {
      userInfo.error = '请先配置115 Cookie。';
      storageInfo.error = '请先配置115 Cookie。';
    } else if (!status.has_client) {
      userInfo.error = '115客户端未连接或Cookie无效。';
      storageInfo.error = '115客户端未连接或Cookie无效。';
    }
  }
  await loadStrmHistory();
});
</script>

<style scoped>
.plugin-app-page-content {
  inline-size: 100%;
  max-inline-size: 100%;
}

/* 系统状态 / 账户：同列等高，底部对齐 */
.dashboard-status-row :deep(.v-col.d-flex) {
  align-self: stretch;
}

.dashboard-status-card {
  min-block-size: 100%;
}

.strm-history-card {
  border-radius: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.1);
  background: rgb(var(--v-theme-surface));
}

.strm-history-card__header {
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.08);
}

/* 与标题区分割线留出间距，避免 outlined 输入框浮动标签与顶线视觉重合 */
.strm-history-card__body {
  padding-block-start: 20px !important;
}

.strm-filter-row {
  padding-block-start: 4px;
}

.strm-kind-field {
  max-width: 280px;
  flex: 1 1 200px;
  min-width: 0;
}

.strm-history-list {
  max-block-size: none;
}

.strm-history-item {
  border-inline-start: 3px solid rgb(var(--v-theme-primary));
  transition: box-shadow 0.2s ease;
}

.strm-history-item:hover {
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.07);
}

.strm-history-item--fail {
  border-inline-start-color: rgb(var(--v-theme-error));
}

.strm-section-label {
  letter-spacing: 0.04em;
  opacity: 0.85;
}

.strm-extra-line {
  word-break: break-word;
  line-height: 1.45;
}
</style>
