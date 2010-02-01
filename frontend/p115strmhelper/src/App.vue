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
}
</style>
