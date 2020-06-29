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
import { ensureSentryInitialized } from '../utils/init-sentry.js';

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
  ensureSentryInitialized();
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
   主题色: #5bcffa (蓝) / #f5abb9 (粉) / #ffb8c9 (粉强调)
   ============================================ */

/* 现代字体栈 */
.dashboard-widget {
  height: 100%;
  width: 100%;
  font-family: 'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
}

/* 卡片入场动画 */
@keyframes cardEnter {
  0% {
    opacity: 0;
    transform: translateY(20px) scale(0.95);
  }

  100% {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

/* 列表项交错入场动画 */
@keyframes listItemEnter {
  0% {
    opacity: 0;
    transform: translateX(-15px);
  }

  100% {
    opacity: 1;
    transform: translateX(0);
  }
}

/* 状态图标脉冲动画 */
@keyframes statusPulse {

  0%,
  100% {
    transform: scale(1);
    filter: drop-shadow(0 0 0 rgba(91, 207, 250, 0));
  }

  50% {
    transform: scale(1.05);
    filter: drop-shadow(0 0 8px rgba(91, 207, 250, 0.4));
  }
}

/* 刷新按钮旋转动画 */
@keyframes refreshSpin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.dashboard-widget :deep(.v-card) {
  border-radius: 20px !important;
  overflow: hidden;
  /* 镜面效果 */
  background: rgba(255, 255, 255, 0.75) !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  box-shadow:
    0 8px 32px rgba(91, 207, 250, 0.2),
    0 2px 8px rgba(245, 171, 185, 0.15),
    inset 0 1px 0 rgba(255, 255, 255, 0.8) !important;
  border: 1px solid rgba(255, 255, 255, 0.4) !important;
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  animation: cardEnter 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
  will-change: transform, box-shadow;
}

.dashboard-widget :deep(.v-card:hover) {
  box-shadow:
    0 16px 40px rgba(91, 207, 250, 0.25),
    0 4px 16px rgba(245, 171, 185, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.9) !important;
  transform: translateY(-6px) scale(1.02);
  background: rgba(255, 255, 255, 0.85) !important;
}

.v-card-item {
  padding-bottom: 8px;
}

/* 标题字体优化 */
:deep(.v-card-title) {
  font-weight: 700 !important;
  font-size: 1.1rem !important;
  letter-spacing: -0.02em !important;
  color: rgba(var(--v-theme-on-surface), 0.9) !important;
  line-height: 1.3 !important;
}

:deep(.v-card-subtitle) {
  font-weight: 400 !important;
  font-size: 0.8rem !important;
  letter-spacing: 0.01em !important;
  color: rgba(var(--v-theme-on-surface), 0.55) !important;
  line-height: 1.4 !important;
}

/* 列表项动画优化 */
:deep(.v-list-item) {
  min-height: 40px;
  border-radius: 10px;
  margin: 3px 6px;
  transition: all 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  opacity: 0;
  animation: listItemEnter 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
  will-change: transform, background;
}

/* 列表项交错动画延迟 */
:deep(.v-list-item:nth-child(1)) {
  animation-delay: 0.1s;
}

:deep(.v-list-item:nth-child(2)) {
  animation-delay: 0.2s;
}

:deep(.v-list-item:nth-child(3)) {
  animation-delay: 0.3s;
}

:deep(.v-list-item:nth-child(4)) {
  animation-delay: 0.4s;
}

:deep(.v-list-item:nth-child(5)) {
  animation-delay: 0.5s;
}

:deep(.v-list-item:hover) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.18) 0%,
      rgba(245, 171, 185, 0.12) 100%) !important;
  backdrop-filter: blur(12px) !important;
  transform: translateX(6px) scale(1.01);
  box-shadow:
    0 4px 12px rgba(91, 207, 250, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.5) !important;
}

/* 列表项标题字体 */
:deep(.v-list-item-title) {
  font-weight: 500 !important;
  font-size: 0.9rem !important;
  letter-spacing: 0 !important;
  line-height: 1.4 !important;
}

/* 状态文字样式 */
:deep(.v-list-item-title .text-success),
:deep(.v-list-item-title .text-grey) {
  font-weight: 600 !important;
  transition: all 0.3s ease;
}

/* 按钮动画优化 */
:deep(.v-btn) {
  border-radius: 10px !important;
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  font-weight: 500 !important;
  letter-spacing: 0.02em !important;
  will-change: transform;
}

:deep(.v-btn:hover) {
  transform: scale(1.08) translateY(-3px);
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.3) 0%,
      rgba(245, 171, 185, 0.25) 100%) !important;
  backdrop-filter: blur(12px) !important;
  box-shadow:
    0 8px 20px rgba(91, 207, 250, 0.4),
    0 4px 12px rgba(245, 171, 185, 0.3),
    inset 0 1px 0 rgba(255, 255, 255, 0.6) !important;
}

:deep(.v-btn:active) {
  transform: scale(0.98) translateY(-1px);
  transition: all 0.1s ease !important;
}

/* 刷新按钮旋转动画 */
:deep(.v-btn[loading] .v-icon) {
  animation: refreshSpin 1s linear infinite;
}

/* 图标动画优化 */
:deep(.v-icon) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform, filter;
}

:deep(.v-list-item:hover .v-icon) {
  transform: scale(1.15) rotate(5deg);
  filter: drop-shadow(0 2px 4px rgba(91, 207, 250, 0.3));
}

/* 状态图标脉冲效果 */
:deep(.v-icon.text-success),
:deep(.v-icon.text-error) {
  animation: statusPulse 2s ease-in-out infinite;
}

/* 刷新状态栏 - 动态镜面效果 */
.refresh-actions {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.12) 0%,
      rgba(245, 171, 185, 0.1) 100%) !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  border-top: 1px solid rgba(255, 255, 255, 0.5) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.6),
    0 -2px 12px rgba(91, 207, 250, 0.12),
    0 -1px 4px rgba(245, 171, 185, 0.08) !important;
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

.refresh-actions:hover {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.18) 0%,
      rgba(245, 171, 185, 0.14) 100%) !important;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 0.7),
    0 -2px 16px rgba(91, 207, 250, 0.18),
    0 -1px 6px rgba(245, 171, 185, 0.12) !important;
}

/* 时间戳文字样式 */
.refresh-actions .text-caption {
  font-weight: 400 !important;
  font-size: 0.75rem !important;
  letter-spacing: 0.02em !important;
  color: rgba(var(--v-theme-on-surface), 0.5) !important;
}

:deep(.refresh-actions .v-divider) {
  border-color: rgba(91, 207, 250, 0.25) !important;
  opacity: 0.5;
}

/* 加载动画优化 */
:deep(.v-progress-circular) {
  transition: all 0.3s ease;
}

/* 错误状态动画 */
.text-error {
  animation: shake 0.5s ease-in-out;
}

@keyframes shake {

  0%,
  100% {
    transform: translateX(0);
  }

  25% {
    transform: translateX(-3px);
  }

  75% {
    transform: translateX(3px);
  }
}

/* 移动端优化 - 保持镜面效果 */
@media (max-width: 768px) {
  .dashboard-widget :deep(.v-card) {
    border-radius: 16px !important;
    backdrop-filter: blur(15px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(15px) saturate(180%) !important;
    animation: cardEnter 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
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
    padding: 10px 14px !important;
    margin: 4px 6px !important;
  }

  /* 优化卡片标题 */
  :deep(.v-card-title) {
    font-size: 1rem !important;
    padding: 12px 14px !important;
    font-weight: 600 !important;
  }

  :deep(.v-card-subtitle) {
    font-size: 0.75rem !important;
    padding: 0 14px 10px 14px !important;
  }

  /* 优化文本大小 */
  :deep(.v-list-item-title) {
    font-size: 0.85rem !important;
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
    padding: 12px !important;
  }

  /* 优化卡片操作区域 */
  :deep(.v-card-actions) {
    padding: 10px 12px !important;
  }

  /* 减少动画复杂度 */
  :deep(.v-list-item:hover) {
    transform: translateX(4px);
  }

  :deep(.v-btn:hover) {
    transform: scale(1.05) translateY(-2px);
  }
}

/* 小屏幕优化 */
@media (max-width: 480px) {
  :deep(.v-card-title) {
    font-size: 0.95rem !important;
  }

  :deep(.v-list-item-title) {
    font-size: 0.8rem !important;
  }
}
</style>