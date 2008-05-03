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
.plugin-app {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: transparent;
}

/* 全局平滑过渡效果 */
* {
  transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
}

/* 优化滚动条样式 */
:deep(::-webkit-scrollbar) {
  width: 8px;
  height: 8px;
}

:deep(::-webkit-scrollbar-track) {
  background: rgba(var(--v-theme-surface), 0.1);
  border-radius: 4px;
}

:deep(::-webkit-scrollbar-thumb) {
  background: rgba(var(--v-theme-on-surface), 0.2);
  border-radius: 4px;
  transition: background 0.2s ease;
}

:deep(::-webkit-scrollbar-thumb:hover) {
  background: rgba(var(--v-theme-primary), 0.4);
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
    -webkit-tap-highlight-color: rgba(var(--v-theme-primary), 0.1);
    tap-highlight-color: rgba(var(--v-theme-primary), 0.1);
  }

  /* 优化文本选择 */
  ::selection {
    background: rgba(var(--v-theme-primary), 0.2);
  }

  ::-moz-selection {
    background: rgba(var(--v-theme-primary), 0.2);
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
}

/* 小屏幕优化 */
@media (max-width: 600px) {
  /* 进一步优化 */
  .plugin-app {
    overflow-x: hidden;
  }
}
</style>
