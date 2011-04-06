<template>
  <div class="dashboard-widget">
    <v-card :flat="!props.config?.attrs?.border" :loading="loading" class="fill-height d-flex flex-column">
      <v-card-item v-if="props.config?.attrs?.title || props.config?.attrs?.subtitle">
        <v-card-title>{{ props.config?.attrs?.title || '115网盘STRM助手' }}</v-card-title>
        <v-card-subtitle v-if="props.config?.attrs?.subtitle">{{ props.config.attrs.subtitle }}</v-card-subtitle>
      </v-card-item>

      <v-card-text class="flex-grow-1 pa-3">
        <!-- 加载中状态 -->
        <div v-if="loading && !initialDataLoaded" class="text-center py-2">
          <v-progress-circular indeterminate color="primary" size="small"></v-progress-circular>
        </div>

        <!-- 错误状态 -->
        <div v-else-if="error" class="text-error text-caption d-flex align-center">
          <v-icon size="small" color="error" class="mr-1">mdi-alert-circle-outline</v-icon>
          {{ error || '数据加载失败' }}
        </div>

        <!-- 数据显示 -->
        <div v-else-if="initialDataLoaded">
          <v-list density="compact" class="py-0">
            <!-- 插件状态显示 -->
            <v-list-item class="pa-0">
              <template v-slot:prepend>
                <v-icon size="small" :color="status.enabled ? 'success' : 'grey'" class="mr-2">
                  {{ status.enabled ? 'mdi-check-circle' : 'mdi-close-circle' }}
                </v-icon>
              </template>
              <v-list-item-title class="text-caption">
                插件状态: <span :class="status.enabled ? 'text-success' : 'text-grey'">
                  {{ status.enabled ? '已启用' : '已禁用' }}
                </span>
              </v-list-item-title>
            </v-list-item>

            <!-- 115客户端状态 -->
            <v-list-item class="pa-0">
              <template v-slot:prepend>
                <v-icon size="small" :color="status.has_client ? 'success' : 'error'" class="mr-2">
                  {{ status.has_client ? 'mdi-account-check' : 'mdi-account-off' }}
                </v-icon>
              </template>
              <v-list-item-title class="text-caption">
                115客户端: <span :class="status.has_client ? 'text-success' : 'text-error'">
                  {{ status.has_client ? '已连接' : '未连接' }}
                </span>
              </v-list-item-title>
            </v-list-item>

            <!-- 任务状态 -->
            <v-list-item class="pa-0">
              <template v-slot:prepend>
                <v-icon size="small" :color="status.running ? 'success' : 'grey'" class="mr-2">
                  {{ status.running ? 'mdi-play-circle' : 'mdi-pause-circle' }}
                </v-icon>
              </template>
              <v-list-item-title class="text-caption">
                任务状态: <span :class="status.running ? 'text-success' : 'text-grey'">
                  {{ status.running ? '运行中' : '空闲' }}
                </span>
              </v-list-item-title>
            </v-list-item>
          </v-list>
        </div>

        <!-- 空数据状态 -->
        <div v-else class="text-caption text-disabled text-center py-2">
          暂无数据
        </div>
      </v-card-text>

      <!-- 刷新按钮 -->
      <v-divider v-if="props.allowRefresh"></v-divider>
      <v-card-actions v-if="props.allowRefresh" class="px-3 py-1 refresh-actions">
        <span class="text-caption text-disabled">{{ lastRefreshedTimeDisplay }}</span>
        <v-spacer></v-spacer>
        <v-btn icon variant="text" size="small" @click="fetchData" :loading="loading">
          <v-icon size="small">mdi-refresh</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue';

// 接收props
const props = defineProps({
  // API对象，用于调用插件API
  api: {
    type: [Object, Function],
    required: true,
  },
  // 配置参数，来自get_dashboard方法的第二个返回值
  config: {
    type: Object,
    default: () => ({ attrs: {} }),
  },
  // 是否允许手动刷新
  allowRefresh: {
    type: Boolean,
    default: false,
  },
  // 自动刷新间隔（秒）
  refreshInterval: {
    type: Number,
    default: 0, // 0表示不自动刷新
  },
});

// 状态变量
const loading = ref(false);
const error = ref(null);
const initialDataLoaded = ref(false);
const lastRefreshedTimestamp = ref(null);

// 状态数据
const status = reactive({
  enabled: false,
  has_client: false,
  running: false,
});

// 刷新计时器
let refreshTimer = null;

// 获取插件ID函数 - 返回固定的插件类名
const getPluginId = () => {
  return "P115StrmHelper";  // 必须与后端插件类名完全匹配
};

// 获取数据的函数
async function fetchData() {
  loading.value = true;
  error.value = null;

  try {
    // 获取插件ID
    const pluginId = getPluginId();

    // 调用API获取状态信息
    const result = await props.api.get(`plugin/${pluginId}/get_status`);

    if (result && result.code === 0 && result.data) {
      // 更新状态数据
      status.enabled = result.data.enabled;
      status.has_client = result.data.has_client;
      status.running = result.data.running;

      initialDataLoaded.value = true;
      lastRefreshedTimestamp.value = Date.now();
    } else {
      throw new Error(result?.msg || '获取状态失败');
    }
  } catch (err) {
    console.error('获取仪表盘数据失败:', err);
    error.value = err.message || '获取数据失败';
  } finally {
    loading.value = false;
  }
}

// 最后刷新时间显示
const lastRefreshedTimeDisplay = computed(() => {
  if (!lastRefreshedTimestamp.value) return '';

  const date = new Date(lastRefreshedTimestamp.value);
  return `更新于: ${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}:${date.getSeconds().toString().padStart(2, '0')}`;
});

// 组件挂载时获取数据
onMounted(() => {
  fetchData();

  // 设置自动刷新
  if (props.refreshInterval > 0) {
    refreshTimer = setInterval(fetchData, props.refreshInterval * 1000);
  }
});

// 组件卸载时清除计时器
onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
});
</script>

<style scoped>
/* ============================================
   仪表盘组件样式 - 镜面效果 + 蓝粉白配色
   ============================================ */

.dashboard-widget {
  height: 100%;
  width: 100%;
}

.dashboard-widget :deep(.v-card) {
  border-radius: 20px !important;
  overflow: hidden;
  /* 镜面效果 */
  background: rgba(255, 255, 255, 0.7) !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  box-shadow:
    0 8px 32px rgba(91, 207, 250, 0.25),
    0 2px 8px rgba(245, 171, 185, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.7) !important;
  border: 1px solid rgba(255, 255, 255, 0.3) !important;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.dashboard-widget :deep(.v-card:hover) {
  box-shadow:
    0 12px 32px rgba(91, 207, 250, 0.15),
    0 4px 12px rgba(245, 171, 185, 0.1),
    inset 0 1px 0 rgba(255, 255, 255, 0.7) !important;
  transform: translateY(-4px) scale(1.02);
  background: rgba(255, 255, 255, 0.75) !important;
}

.v-card-item {
  padding-bottom: 8px;
}

:deep(.v-card-title) {
  font-weight: 600 !important;
  color: rgba(var(--v-theme-on-surface), 0.87) !important;
}

:deep(.v-card-subtitle) {
  color: rgba(var(--v-theme-on-surface), 0.6) !important;
}

:deep(.v-list-item) {
  min-height: 36px;
  border-radius: 8px;
  margin: 2px 4px;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

:deep(.v-list-item:hover) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.2) 0%,
      rgba(245, 171, 185, 0.15) 100%) !important;
  backdrop-filter: blur(10px) !important;
  transform: translateX(4px);
  box-shadow:
    0 2px 8px rgba(91, 207, 250, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.4) !important;
}

:deep(.v-list-item-title) {
  font-weight: 500 !important;
}

:deep(.v-btn) {
  border-radius: 8px !important;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

:deep(.v-btn:hover) {
  transform: scale(1.1) translateY(-2px);
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.25) 0%,
      rgba(245, 171, 185, 0.2) 100%) !important;
  backdrop-filter: blur(10px) !important;
  box-shadow:
    0 6px 16px rgba(91, 207, 250, 0.35),
    0 2px 8px rgba(245, 171, 185, 0.3),
    inset 0 1px 0 rgba(255, 255, 255, 0.5) !important;
}

:deep(.v-icon) {
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

/* 刷新状态栏 - 动态镜面效果 */
.refresh-actions {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.15) 0%,
      rgba(245, 171, 185, 0.12) 100%) !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  border-top: 1px solid rgba(255, 255, 255, 0.4) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.5),
    0 -2px 12px rgba(91, 207, 250, 0.15),
    0 -1px 4px rgba(245, 171, 185, 0.1) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.refresh-actions:hover {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.2) 0%,
      rgba(245, 171, 185, 0.15) 100%) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 -2px 16px rgba(91, 207, 250, 0.2),
    0 -1px 6px rgba(245, 171, 185, 0.15) !important;
}

:deep(.refresh-actions .v-divider) {
  border-color: rgba(91, 207, 250, 0.3) !important;
  opacity: 0.6;
}

/* 移动端优化 - 保持镜面效果 */
@media (max-width: 768px) {
  .dashboard-widget :deep(.v-card) {
    border-radius: 16px !important;
    backdrop-filter: blur(15px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(15px) saturate(180%) !important;
  }

  /* 优化触摸目标大小 */
  :deep(.v-btn) {
    min-height: 44px !important;
    min-width: 44px !important;
  }

  :deep(.v-btn--icon) {
    min-width: 44px !important;
    min-height: 44px !important;
  }

  /* 优化列表项触摸区域 */
  :deep(.v-list-item) {
    min-height: 48px !important;
    padding: 8px 12px !important;
    margin: 4px 2px !important;
  }

  /* 优化卡片标题 */
  :deep(.v-card-title) {
    font-size: 0.875rem !important;
    padding: 10px 12px !important;
  }

  :deep(.v-card-subtitle) {
    font-size: 0.75rem !important;
    padding: 0 12px 8px 12px !important;
  }

  /* 优化文本大小 */
  :deep(.v-list-item-title) {
    font-size: 0.8rem !important;
  }

  /* 优化图标大小 */
  :deep(.v-icon) {
    font-size: 20px !important;
  }

  :deep(.v-icon--size-small) {
    font-size: 18px !important;
  }

  /* 优化卡片内容区域 */
  :deep(.v-card-text) {
    padding: 10px !important;
  }

  /* 优化卡片操作区域 */
  :deep(.v-card-actions) {
    padding: 8px 10px !important;
  }
}
</style>