<template>
  <v-app>
    <div class="plugin-app">
      <component :is="currentComponent" :api="api" @switch="switchComponent" @close="closeModal" @save="saveConfig">
      </component>
    </div>
  </v-app>
</template>

<script>
import { defineComponent, ref, shallowRef, onMounted, onBeforeUnmount } from 'vue';
import Page from './components/Page.vue';
import Config from './components/Config.vue';

export default defineComponent({
  name: 'App',

  setup() {
    // 当前显示的组件
    const currentComponent = shallowRef(Page);
    // API对象，用于传递给子组件
    const api = ref(null);

    // 处理窗口消息
    const handleMessage = (event) => {
      // 接收来自父窗口的消息，获取API对象
      if (event.data && event.data.type === 'api') {
        api.value = event.data.data;
        console.log('收到API:', api.value);
      }

      // 处理显示配置页面的消息
      if (event.data && event.data.type === 'showConfig') {
        currentComponent.value = Config;
      }
    };

    // 切换组件
    const switchComponent = () => {
      currentComponent.value = currentComponent.value === Page ? Config : Page;
    };

    // 关闭模态框
    const closeModal = () => {
      if (window.parent && window.parent.postMessage) {
        window.parent.postMessage({ type: 'close' }, '*');
      }
    };

    // 保存配置
    const saveConfig = (config) => {
      if (window.parent && window.parent.postMessage) {
        window.parent.postMessage({ type: 'save', data: config }, '*');
      }
      // 保存后切换到Page组件
      currentComponent.value = Page;
    };

    // 挂载时添加消息监听
    onMounted(() => {
      window.addEventListener('message', handleMessage);

      // 通知父窗口已准备好接收API
      if (window.parent && window.parent.postMessage) {
        window.parent.postMessage({ type: 'ready' }, '*');
      }
    });

    // 卸载前移除消息监听
    onBeforeUnmount(() => {
      window.removeEventListener('message', handleMessage);
    });

    return {
      currentComponent,
      api,
      switchComponent,
      closeModal,
      saveConfig
    };
  }
});
</script>

<style>
/* ============================================
   全局样式 - 镜面效果 + 蓝粉白配色
   主题色: #5bcffa (蓝) / #f5abb9 (粉) / #ffb8c9 (粉强调)
   ============================================ */

/* 全局背景渐变 - 蓝粉白配色（增强对比度） */
:deep(.v-application) {
  background: linear-gradient(135deg, #D0EFFF 0%, #FFE5EB 50%, #FFFFFF 100%) !important;
  background-attachment: fixed;
  min-height: 100vh;
}

.plugin-app {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: transparent;
  position: relative;
}

/* 添加动态背景装饰 */
.plugin-app::before {
  content: '';
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background:
    radial-gradient(circle at 20% 50%, rgba(91, 207, 250, 0.2) 0%, transparent 50%),
    radial-gradient(circle at 80% 80%, rgba(245, 171, 185, 0.2) 0%, transparent 50%),
    radial-gradient(circle at 40% 20%, rgba(255, 184, 201, 0.15) 0%, transparent 50%);
  pointer-events: none;
  z-index: 0;
}

.plugin-app>* {
  position: relative;
  z-index: 1;
}

/* 全局平滑过渡效果 */
* {
  transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
}

/* 优化滚动条样式 - 蓝粉配色 */
:deep(::-webkit-scrollbar) {
  width: 8px;
  height: 8px;
}

:deep(::-webkit-scrollbar-track) {
  background: rgba(91, 207, 250, 0.05);
  border-radius: 4px;
}

:deep(::-webkit-scrollbar-thumb) {
  background: linear-gradient(135deg, rgba(91, 207, 250, 0.7), rgba(245, 171, 185, 0.7));
  border-radius: 4px;
  transition: background 0.2s ease;
}

:deep(::-webkit-scrollbar-thumb:hover) {
  background: linear-gradient(135deg, rgba(91, 207, 250, 0.9), rgba(245, 171, 185, 0.9));
}

/* 移动端优化 */
@media (max-width: 768px) {

  /* 优化滚动条在移动端（更细） */
  :deep(::-webkit-scrollbar) {
    width: 4px;
    height: 4px;
  }

  /* 确保触摸事件正常工作 */
  * {
    -webkit-tap-highlight-color: rgba(91, 207, 250, 0.1);
    tap-highlight-color: rgba(91, 207, 250, 0.1);
  }

  /* 优化文本选择 - 动态适配主题 */
  ::selection {
    background: rgba(91, 207, 250, 0.3);
    color: inherit;
  }

  ::-moz-selection {
    background: rgba(91, 207, 250, 0.3);
    color: inherit;
  }

  /* 防止文本在移动端被放大 */
  input[type="text"],
  input[type="password"],
  input[type="email"],
  input[type="number"],
  textarea,
  select {
    font-size: 16px !important;
  }

  /* 移动端背景优化 */
  :deep(.v-application) {
    background: linear-gradient(135deg, #D0EFFF 0%, #FFE5EB 50%, #FFFFFF 100%) !important;
  }
}

/* 小屏幕优化 */
@media (max-width: 600px) {

  /* 进一步优化 */
  .plugin-app {
    overflow-x: hidden;
  }
}

/* 全局菜单和弹出层镜面效果 - 动态适配主题 */
:deep(.v-menu .v-overlay__content),
:deep(.v-dialog .v-overlay__content),
:deep(.v-navigation-drawer),
:deep(.v-sheet.v-menu) {
  background: rgba(var(--v-theme-surface), 0.85) !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12) !important;
  box-shadow:
    0 8px 32px rgba(91, 207, 250, 0.25),
    0 2px 8px rgba(245, 171, 185, 0.2),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.05) !important;
  border-radius: 16px !important;
}

/* 暗色模式下的菜单和弹出层 - 使用CSS变量简化 */
@media (prefers-color-scheme: dark) {

  :deep(.v-menu .v-overlay__content),
  :deep(.v-dialog .v-overlay__content),
  :deep(.v-navigation-drawer),
  :deep(.v-sheet.v-menu) {
    background: rgba(var(--v-theme-surface), 0.9) !important;
    border: 1px solid rgba(255, 255, 255, 0.15) !important;
    box-shadow:
      0 8px 32px rgba(91, 207, 250, 0.3),
      0 2px 8px rgba(245, 171, 185, 0.2),
      inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
  }
}

:deep(.v-theme--dark) .v-menu .v-overlay__content,
:deep(.v-theme--dark) .v-dialog .v-overlay__content,
:deep(.v-theme--dark) .v-navigation-drawer,
:deep(.v-theme--dark) .v-sheet.v-menu,
:deep([data-theme="dark"]) .v-menu .v-overlay__content,
:deep([data-theme="dark"]) .v-dialog .v-overlay__content,
:deep([data-theme="dark"]) .v-navigation-drawer,
:deep([data-theme="dark"]) .v-sheet.v-menu {
  background: rgba(var(--v-theme-surface), 0.9) !important;
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  box-shadow:
    0 8px 32px rgba(91, 207, 250, 0.3),
    0 2px 8px rgba(245, 171, 185, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
}

/* 优化数据表格的镜面效果 - 动态适配主题 */
:deep(.v-data-table) {
  background: rgba(var(--v-theme-surface), 0.5) !important;
  backdrop-filter: blur(10px) !important;
  border-radius: 12px !important;
}

/* 暗色模式下的数据表格 */
:deep(.v-theme--dark) .v-data-table,
:deep([data-theme="dark"]) .v-data-table {
  background: rgba(var(--v-theme-surface), 0.6) !important;
}

:deep(.v-data-table__thead th) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.2) 0%,
      rgba(245, 171, 185, 0.15) 100%) !important;
  backdrop-filter: blur(10px) !important;
}

/* 优化进度条的镜面效果 - 动态适配主题 */
:deep(.v-progress-linear) {
  border-radius: 10px !important;
  overflow: hidden;
  background: rgba(var(--v-theme-surface), 0.3) !important;
  backdrop-filter: blur(5px) !important;
}

/* 优化芯片组 - 动态适配主题 */
:deep(.v-chip-group) {
  gap: 8px;
}

:deep(.v-chip-group .v-chip) {
  background: rgba(var(--v-theme-surface), 0.6) !important;
  backdrop-filter: blur(10px) !important;
}

/* 列表项选中状态 - 动态适配主题 */
:deep(.v-list-item--active),
:deep(.v-list-item[aria-selected="true"]),
:deep(.v-item--active) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.2) 0%,
      rgba(245, 171, 185, 0.15) 100%) !important;
  backdrop-filter: blur(10px) !important;
  color: rgba(var(--v-theme-on-surface), 0.87) !important;
}

/* 暗色模式下的列表项选中状态 */
:deep(.v-theme--dark) .v-list-item--active,
:deep(.v-theme--dark) .v-list-item[aria-selected="true"],
:deep(.v-theme--dark) .v-item--active,
:deep([data-theme="dark"]) .v-list-item--active,
:deep([data-theme="dark"]) .v-list-item[aria-selected="true"],
:deep([data-theme="dark"]) .v-item--active {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.3) 0%,
      rgba(245, 171, 185, 0.25) 100%) !important;
  color: rgba(255, 255, 255, 0.9) !important;
}

/* 芯片选中状态 - 动态适配主题 */
:deep(.v-chip--selected) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.25) 0%,
      rgba(245, 171, 185, 0.2) 100%) !important;
  backdrop-filter: blur(10px) !important;
  color: rgba(var(--v-theme-on-surface), 0.87) !important;
}

/* 暗色模式下的芯片选中状态 */
:deep(.v-theme--dark) .v-chip--selected,
:deep([data-theme="dark"]) .v-chip--selected {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.35) 0%,
      rgba(245, 171, 185, 0.3) 100%) !important;
  color: rgba(255, 255, 255, 0.9) !important;
}

/* Tab 选中状态 - 动态适配主题 */
:deep(.v-tab--selected) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.3) 0%,
      rgba(245, 171, 185, 0.25) 100%) !important;
  color: #5bcffa !important;
}

/* 暗色模式下的 Tab 选中状态 */
:deep(.v-theme--dark) .v-tab--selected,
:deep([data-theme="dark"]) .v-tab--selected {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.4) 0%,
      rgba(245, 171, 185, 0.35) 100%) !important;
  color: #5bcffa !important;
}

/* 输入框选中/聚焦状态 - 动态适配主题 */
:deep(.v-field--focused),
:deep(.v-field--active) {
  box-shadow:
    0 0 0 3px rgba(91, 207, 250, 0.3),
    0 4px 12px rgba(245, 171, 185, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.6) !important;
  border-color: rgba(91, 207, 250, 0.6) !important;
}

/* 暗色模式下的输入框聚焦状态 */
:deep(.v-theme--dark) .v-field--focused,
:deep(.v-theme--dark) .v-field--active,
:deep([data-theme="dark"]) .v-field--focused,
:deep([data-theme="dark"]) .v-field--active {
  box-shadow:
    0 0 0 3px rgba(91, 207, 250, 0.4),
    0 4px 12px rgba(245, 171, 185, 0.35),
    inset 0 1px 0 rgba(255, 255, 255, 0.2) !important;
  border-color: rgba(91, 207, 250, 0.7) !important;
}

/* 表格行选中状态 - 动态适配主题 */
:deep(.v-data-table__tr--selected),
:deep(.v-data-table__tr[aria-selected="true"]) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.15) 0%,
      rgba(245, 171, 185, 0.1) 100%) !important;
}

/* 暗色模式下的表格行选中状态 */
:deep(.v-theme--dark) .v-data-table__tr--selected,
:deep(.v-theme--dark) .v-data-table__tr[aria-selected="true"],
:deep([data-theme="dark"]) .v-data-table__tr--selected,
:deep([data-theme="dark"]) .v-data-table__tr[aria-selected="true"] {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.25) 0%,
      rgba(245, 171, 185, 0.2) 100%) !important;
}

/* 复选框和单选框选中状态 - 动态适配主题 */
:deep(.v-selection-control--dirty .v-selection-control__input) {
  color: rgb(var(--v-theme-primary)) !important;
}

/* 暗色模式下的复选框和单选框 */
:deep(.v-theme--dark) .v-selection-control--dirty .v-selection-control__input,
:deep([data-theme="dark"]) .v-selection-control--dirty .v-selection-control__input {
  color: #5bcffa !important;
}
</style>
