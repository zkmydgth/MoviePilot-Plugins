import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {resolveComponent:_resolveComponent,createVNode:_createVNode,createElementVNode:_createElementVNode,withCtx:_withCtx,toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createElementBlock:_createElementBlock,renderList:_renderList,Fragment:_Fragment,normalizeClass:_normalizeClass,withKeys:_withKeys} = await importShared('vue');


const _hoisted_1 = { class: "plugin-page" };
const _hoisted_2 = {
  key: 3,
  class: "my-1"
};
const _hoisted_3 = { key: 1 };
const _hoisted_4 = {
  class: "path-mapping-item pa-2 border rounded-sm",
  style: {"background-color":"rgba(var(--v-theme-on-surface), 0.02)"}
};
const _hoisted_5 = ["title"];
const _hoisted_6 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_7 = ["title"];
const _hoisted_8 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_9 = {
  class: "path-mapping-item pa-2 border rounded-sm",
  style: {"background-color":"rgba(var(--v-theme-on-surface), 0.02)"}
};
const _hoisted_10 = ["title"];
const _hoisted_11 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_12 = ["title"];
const _hoisted_13 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_14 = {
  class: "path-mapping-item pa-2 border rounded-sm",
  style: {"background-color":"rgba(var(--v-theme-on-surface), 0.02)"}
};
const _hoisted_15 = ["title"];
const _hoisted_16 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_17 = ["title"];
const _hoisted_18 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_19 = {
  class: "path-mapping-item pa-2 border rounded-sm",
  style: {"background-color":"rgba(var(--v-theme-on-surface), 0.02)"}
};
const _hoisted_20 = ["title"];
const _hoisted_21 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_22 = ["title"];
const _hoisted_23 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_24 = {
  class: "path-item pa-2 border rounded-sm",
  style: {"background-color":"rgba(var(--v-theme-on-surface), 0.02)"}
};
const _hoisted_25 = { class: "d-flex align-center" };
const _hoisted_26 = ["title"];
const _hoisted_27 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_28 = {
  class: "path-group-item pa-2 border rounded-sm",
  style: {"background-color":"rgba(var(--v-theme-on-surface), 0.02)"}
};
const _hoisted_29 = ["title"];
const _hoisted_30 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_31 = ["title"];
const _hoisted_32 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_33 = {
  key: 0,
  class: "mt-1 pt-1",
  style: {"border-top":"1px dashed rgba(var(--v-border-color), 0.2)"}
};
const _hoisted_34 = {
  key: 0,
  class: "d-flex align-center"
};
const _hoisted_35 = ["title"];
const _hoisted_36 = {
  class: "text-caption",
  style: {"line-height":"1.2"}
};
const _hoisted_37 = { class: "d-flex" };
const _hoisted_38 = { class: "text-body-2" };
const _hoisted_39 = { class: "d-flex" };
const _hoisted_40 = { class: "text-body-2" };
const _hoisted_41 = { class: "d-flex align-center" };
const _hoisted_42 = {
  key: 0,
  class: "d-flex justify-center my-3"
};
const _hoisted_43 = { key: 1 };

const {ref,reactive,computed,onMounted,watch} = await importShared('vue');



const _sfc_main = {
  __name: 'Page',
  props: {
  api: {
    type: [Object, Function],
    required: true
  },
  // 接收从父组件传递的配置数据
  initialConfig: {
    type: Object,
    default: () => ({})
  }
},
  emits: ['close', 'switch', 'update:config', 'action'],
  setup(__props, { emit: __emit }) {

const props = __props;

const emit = __emit;

// 状态变量
const loading = ref(true);
const refreshing = ref(false);
const syncLoading = ref(false);
const shareSyncLoading = ref(false);
const initialDataLoaded = ref(false);
const error = ref(null);
const actionMessage = ref(null);
const actionMessageType = ref('info');
const actionLoading = ref(false);

const status = reactive({
  enabled: false,
  has_client: false,
  running: false
});

const userInfo = reactive({
  name: null,
  is_vip: null,
  is_forever_vip: null,
  vip_expire_date: null,
  avatar: null,
  error: null,
  loading: true
});

const storageInfo = reactive({
  total: null,
  used: null,
  remaining: null,
  error: null,
  loading: true
});

// 辅助函数，用于计算存储百分比
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

// 计算属性：路径配置是否完整
const isProperlyCongifured = computed(() => {
  if (!props.initialConfig) return false;

  const hasBasicConfig = props.initialConfig.enabled && props.initialConfig.cookies && props.initialConfig.moviepilot_address;
  if (!hasBasicConfig) return false;

  // 至少一个功能区域配置了路径
  const hasTransferPaths = getPathsCount(props.initialConfig.transfer_monitor_paths) > 0 && props.initialConfig.transfer_monitor_enabled;
  const hasFullSyncPaths = getPathsCount(props.initialConfig.full_sync_strm_paths) > 0 && (props.initialConfig.timing_full_sync_strm);
  const hasIncrementSyncPaths = getPathsCount(props.initialConfig.increment_sync_strm_paths) > 0 && (props.initialConfig.increment_sync_strm_enabled);
  const hasLifePaths = getPathsCount(props.initialConfig.monitor_life_paths) > 0 && props.initialConfig.monitor_life_enabled;
  const hasSharePaths = props.initialConfig.user_share_local_path && props.initialConfig.user_share_pan_path;

  return hasTransferPaths || hasFullSyncPaths || hasIncrementSyncPaths || hasLifePaths || hasSharePaths;
});

// 计算路径数量
const getPathsCount = (pathString) => {
  if (!pathString) return 0;

  try {
    // 根据换行符拆分路径字符串，并过滤掉空行
    const paths = pathString.split('\n').filter(line => line.trim() && line.includes('#'));
    return paths.length;
  } catch (e) {
    console.error('解析路径字符串失败:', e);
    return 0;
  }
};

// 新增：获取网盘整理路径数量的辅助函数
const getPanTransferPathsCount = (pathString) => {
  if (!pathString) return 0;
  try {
    const paths = pathString.split('\n').filter(line => line.trim());
    return paths.length;
  } catch (e) {
    console.error('解析网盘整理路径字符串失败:', e);
    return 0;
  }
};

// 获取插件状态
const getStatus = async () => {
  loading.value = true;
  error.value = null;

  try {
    // 获取插件ID
    const pluginId = "P115StrmHelper";

    // 调用API获取状态
    const result = await props.api.get(`plugin/${pluginId}/get_status`);

    if (result && result.code === 0 && result.data) {
      // 确保从API获取实际状态，而不是使用默认值
      status.enabled = Boolean(result.data.enabled);
      status.has_client = Boolean(result.data.has_client);
      status.running = Boolean(result.data.running);

      // 同时获取并更新配置信息到props.initialConfig
      try {
        const configData = await props.api.get(`plugin/${pluginId}/get_config`);
        if (configData) {
          // 更新配置对象
          Object.assign(props.initialConfig, configData);
          console.log('已获取最新配置:', props.initialConfig);
        }
      } catch (configErr) {
        console.error('获取配置失败:', configErr);
      }

      initialDataLoaded.value = true;
    } else {
      // 如果API调用失败但有initialConfig，使用它的状态
      if (props.initialConfig) {
        status.enabled = Boolean(props.initialConfig.enabled);
        // 检查是否真的有有效的Cookie
        status.has_client = Boolean(props.initialConfig.cookies && props.initialConfig.cookies.trim() !== '');
        status.running = false;
        initialDataLoaded.value = true;

        // 如果initialConfig是空的，尝试获取配置
        if (Object.keys(props.initialConfig).length <= 1) {
          try {
            const configData = await props.api.get(`plugin/${pluginId}/get_config`);
            if (configData) {
              Object.assign(props.initialConfig, configData);
              console.log('从配置API获取配置:', props.initialConfig);
            }
          } catch (configErr) {
            console.error('获取配置失败:', configErr);
          }
        }

        throw new Error('状态API调用失败，使用配置数据显示状态');
      } else {
        throw new Error(result?.msg || '获取状态失败，请检查网络连接');
      }
    }
  } catch (err) {
    if (!err.message.includes('使用配置数据显示状态')) {
      error.value = `获取状态失败: ${err.message || '未知错误'}`;
    }
    console.error('获取状态失败:', err);
  } finally {
    loading.value = false;
  }
};

// 刷新状态
const refreshStatus = async () => {
  refreshing.value = true;
  await getStatus();
  if (status.has_client && props.initialConfig?.cookies) {
    await fetchUserStorageStatus();
  } else {
    userInfo.loading = false;
    storageInfo.loading = false;
    if (!props.initialConfig?.cookies) {
      userInfo.error = "请先配置115 Cookie。";
      storageInfo.error = "请先配置115 Cookie。";
    } else if (!status.has_client) {
      userInfo.error = "115客户端未连接或Cookie无效。";
      storageInfo.error = "115客户端未连接或Cookie无效。";
    }
  }
  refreshing.value = false;

  actionMessage.value = '状态已刷新';
  actionMessageType.value = 'success';

  // 3秒后清除消息
  setTimeout(() => {
    actionMessage.value = null;
  }, 3000);
};

// 触发全量同步
const triggerFullSync = async () => {
  syncLoading.value = true;
  actionLoading.value = true;
  error.value = null;
  actionMessage.value = null;

  try {
    // 检查状态
    if (!status.enabled) {
      throw new Error('插件未启用，请先在配置页面启用插件');
    }

    if (!status.has_client) {
      throw new Error('插件未配置Cookie或Cookie无效，请先在配置页面设置115 Cookie');
    }

    if (getPathsCount(props.initialConfig?.full_sync_strm_paths) === 0) {
      throw new Error('未配置全量同步路径，请先在配置页面设置同步路径');
    }

    // 获取插件ID
    const pluginId = "P115StrmHelper";

    // 调用API触发全量同步
    const result = await props.api.post(`plugin/${pluginId}/full_sync`);

    if (result && result.code === 0) {
      actionMessage.value = result.msg || '全量同步任务已启动';
      actionMessageType.value = 'success';
      // 刷新状态
      await getStatus();
    } else {
      throw new Error(result?.msg || '启动全量同步失败');
    }
  } catch (err) {
    error.value = `启动全量同步失败: ${err.message || '未知错误'}`;
    console.error('启动全量同步失败:', err);
  } finally {
    syncLoading.value = false;
    actionLoading.value = false;
  }
};

// 分享同步对话框
const shareDialog = reactive({
  show: false,
  error: null,
  shareLink: '',
  shareCode: '',
  receiveCode: '',
  panPath: '/',
  localPath: '',
  downloadMediaInfo: false
});

// 计算属性：分享对话框是否填写有效
const isShareDialogValid = computed(() => {
  // 必须有本地路径
  if (!shareDialog.localPath) return false;

  // 必须有分享链接或分享码，如果有分享码则必须有分享密码
  if (!shareDialog.shareLink && !shareDialog.shareCode) return false;
  if (shareDialog.shareCode && !shareDialog.receiveCode) return false;

  return true;
});

// 目录选择器对话框
const dirDialog = reactive({
  show: false,
  isLocal: true,
  loading: false,
  error: null,
  currentPath: '/',
  items: [],
  selectedPath: '',
  callback: null
});

// 打开分享同步对话框
const openShareDialog = () => {
  shareDialog.show = true;
  shareDialog.error = null;

  // 从配置中加载值
  if (props.initialConfig) {
    shareDialog.shareLink = props.initialConfig.user_share_link || '';
    shareDialog.shareCode = props.initialConfig.user_share_code || '';
    shareDialog.receiveCode = props.initialConfig.user_receive_code || '';
    shareDialog.panPath = props.initialConfig.user_share_pan_path || '/';
    shareDialog.localPath = props.initialConfig.user_share_local_path || '';
    shareDialog.downloadMediaInfo = props.initialConfig.share_strm_auto_download_mediainfo_enabled || false;
  }
};

// 关闭分享同步对话框
const closeShareDialog = () => {
  shareDialog.show = false;
};

// 打开目录选择器
const openShareDirSelector = (type) => {
  dirDialog.show = true;
  dirDialog.isLocal = type === 'local';
  dirDialog.loading = false;
  dirDialog.error = null;
  dirDialog.items = [];

  // 设置初始路径
  if (dirDialog.isLocal) {
    dirDialog.currentPath = shareDialog.localPath || '/';
  } else {
    dirDialog.currentPath = shareDialog.panPath || '/';
  }

  // 设置回调函数
  dirDialog.callback = (path) => {
    if (dirDialog.isLocal) {
      shareDialog.localPath = path;
    } else {
      shareDialog.panPath = path;
    }
  };

  // 加载目录内容
  loadDirContent();
};

// 加载目录内容
const loadDirContent = async () => {
  dirDialog.loading = true;
  dirDialog.error = null;
  dirDialog.items = [];

  try {
    // 本地目录浏览
    if (dirDialog.isLocal) {
      try {
        // 使用MoviePilot的文件管理API
        const response = await props.api.post('storage/list', {
          path: dirDialog.currentPath || '/',
          type: 'share', // 使用默认的share类型
          flag: 'ROOT'
        });

        if (response && Array.isArray(response)) {
          dirDialog.items = response
            .filter(item => item.type === 'dir') // 只保留目录
            .map(item => ({
              name: item.name,
              path: item.path,
              is_dir: true
            }))
            .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
        } else {
          throw new Error('浏览目录失败：无效响应');
        }
      } catch (error) {
        console.error('浏览本地目录失败:', error);
        dirDialog.error = `浏览本地目录失败: ${error.message || '未知错误'}`;
        dirDialog.items = [];
      }
    }
    // 115网盘目录浏览
    else {
      // 获取插件ID
      const pluginId = "P115StrmHelper";

      // 检查cookie是否已设置
      if (!props.initialConfig?.cookies || props.initialConfig?.cookies.trim() === '') {
        throw new Error('请先设置115 Cookie才能浏览网盘目录');
      }

      // 调用API获取目录内容
      const result = await props.api.get(`plugin/${pluginId}/browse_dir?path=${encodeURIComponent(dirDialog.currentPath)}&is_local=${dirDialog.isLocal}`);

      if (result && result.code === 0 && result.items) {
        dirDialog.items = result.items.filter(item => item.is_dir); // 只保留目录
        dirDialog.currentPath = result.path || dirDialog.currentPath;
      } else {
        throw new Error(result?.msg || '获取网盘目录内容失败');
      }
    }
  } catch (error) {
    console.error('加载目录内容失败:', error);
    dirDialog.error = error.message || '获取目录内容失败';

    // 如果是Cookie错误，清空目录列表
    if (error.message.includes('Cookie') || error.message.includes('cookie')) {
      dirDialog.items = [];
    }
  } finally {
    dirDialog.loading = false;
  }
};

// 选择目录
const selectDir = (item) => {
  if (!item || !item.is_dir) return;

  if (item.path) {
    dirDialog.currentPath = item.path;
    loadDirContent();
  }
};

// 导航到父目录
const navigateToParentDir = () => {
  const path = dirDialog.currentPath;

  if (path === '/' || path === 'C:\\' || path === 'C:/') return;

  // 统一使用正斜杠处理路径
  const normalizedPath = path.replace(/\\/g, '/');
  const parts = normalizedPath.split('/').filter(Boolean);

  if (parts.length === 0) {
    dirDialog.currentPath = '/';
  } else if (parts.length === 1 && normalizedPath.includes(':')) {
    // Windows驱动器根目录
    dirDialog.currentPath = parts[0] + ':/';
  } else {
    // 移除最后一个部分
    parts.pop();
    dirDialog.currentPath = parts.length === 0 ? '/' :
      (normalizedPath.startsWith('/') ? '/' : '') +
      parts.join('/') + '/';
  }

  loadDirContent();
};

// 确认目录选择
const confirmDirSelection = () => {
  if (!dirDialog.currentPath) return;

  let processedPath = dirDialog.currentPath;
  // 移除末尾的斜杠，除非路径是 "/" 或者类似 "C:/" 的驱动器根目录
  if (processedPath !== '/' &&
    !(/^[a-zA-Z]:[\\\/]$/.test(processedPath)) &&
    (processedPath.endsWith('/') || processedPath.endsWith('\\\\'))) {
    processedPath = processedPath.slice(0, -1);
  }

  if (typeof dirDialog.callback === 'function') {
    dirDialog.callback(processedPath);
  }

  // 关闭对话框
  closeDirDialog();
};

// 关闭目录选择器对话框
const closeDirDialog = () => {
  dirDialog.show = false;
  dirDialog.items = [];
  dirDialog.error = null;
};

// 执行分享同步
const executeShareSync = async () => {
  shareSyncLoading.value = true;
  shareDialog.error = null;

  try {
    // 检查必填项
    if (!shareDialog.localPath) {
      throw new Error('请先设置本地生成STRM路径');
    }

    if (!shareDialog.shareLink && !shareDialog.shareCode) {
      throw new Error('请输入115网盘分享链接或分享码');
    }

    if (shareDialog.shareCode && !shareDialog.receiveCode) {
      throw new Error('使用分享码时必须输入分享密码');
    }

    // 获取插件ID
    const pluginId = "P115StrmHelper";

    // 首先保存配置
    if (props.initialConfig) {
      // 更新配置
      props.initialConfig.user_share_link = shareDialog.shareLink;
      props.initialConfig.user_share_code = shareDialog.shareCode;
      props.initialConfig.user_receive_code = shareDialog.receiveCode;
      props.initialConfig.user_share_pan_path = shareDialog.panPath;
      props.initialConfig.user_share_local_path = shareDialog.localPath;
      props.initialConfig.share_strm_auto_download_mediainfo_enabled = shareDialog.downloadMediaInfo;

      // 保存配置
      await props.api.post(`plugin/${pluginId}/save_config`, props.initialConfig);
    }

    // 调用API触发分享同步
    const result = await props.api.post(`plugin/${pluginId}/share_sync`);

    if (result && result.code === 0) {
      actionMessage.value = result.msg || '分享同步任务已启动';
      actionMessageType.value = 'success';

      // 刷新状态
      await getStatus();

      // 关闭对话框
      closeShareDialog();
    } else {
      throw new Error(result?.msg || '启动分享同步失败');
    }
  } catch (err) {
    shareDialog.error = `启动分享同步失败: ${err.message || '未知错误'}`;
    console.error('启动分享同步失败:', err);
  } finally {
    shareSyncLoading.value = false;
  }
};

// 添加路径解析函数
const getParsedPaths = (pathString) => {
  if (!pathString) return [];

  try {
    // 根据换行符拆分路径字符串，并过滤掉空行
    const paths = pathString.split('\n').filter(line => line.trim() && line.includes('#'));
    return paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });
  } catch (e) {
    console.error('解析路径字符串失败:', e);
    return [];
  }
};

// 新增：解析网盘整理路径的辅助函数
const getParsedPanTransferPaths = (pathString) => {
  if (!pathString) return [];
  try {
    const paths = pathString.split('\n').filter(line => line.trim());
    return paths.map(path => ({ path }));
  } catch (e) {
    console.error('解析网盘整理路径字符串失败:', e);
    return [];
  }
};

// 当initialConfig变化时更新状态
watch(() => props.initialConfig, (newConfig) => {
  if (newConfig) {
    status.enabled = newConfig.enabled || false;
    status.has_client = Boolean(newConfig.cookies && newConfig.cookies.trim() !== '');
  }
}, { immediate: true });

// 组件生命周期
onMounted(async () => {
  await getStatus();
  if (status.has_client && props.initialConfig?.cookies) {
    await fetchUserStorageStatus();
  } else {
    userInfo.loading = false;
    storageInfo.loading = false;
    if (!props.initialConfig?.cookies) {
      userInfo.error = "请先配置115 Cookie。";
      storageInfo.error = "请先配置115 Cookie。";
    } else if (!status.has_client) {
      userInfo.error = "115客户端未连接或Cookie无效。";
      storageInfo.error = "115客户端未连接或Cookie无效。";
    }
  }
});

async function fetchUserStorageStatus() {
  userInfo.loading = true;
  userInfo.error = null;
  storageInfo.loading = true;
  storageInfo.error = null;

  try {
    const pluginId = "P115StrmHelper";
    const response = await props.api.get(`plugin/${pluginId}/user_storage_status`);

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
      if (errMsg.includes("Cookie") || errMsg.includes("未配置")) {
        status.has_client = false;
      }
    }
  } catch (err) {
    console.error('获取用户/存储状态失败:', err);
    const Mgs = `请求用户/存储状态时出错: ${err.message || '未知网络错误'}`;
    userInfo.error = Mgs;
    storageInfo.error = Mgs;
  } finally {
    userInfo.loading = false;
    storageInfo.loading = false;
  }
}

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_card_title = _resolveComponent("v-card-title");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_skeleton_loader = _resolveComponent("v-skeleton-loader");
  const _component_v_list_item_title = _resolveComponent("v-list-item-title");
  const _component_v_chip = _resolveComponent("v-chip");
  const _component_v_list_item = _resolveComponent("v-list-item");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_list = _resolveComponent("v-list");
  const _component_v_card_text = _resolveComponent("v-card-text");
  const _component_v_card = _resolveComponent("v-card");
  const _component_v_img = _resolveComponent("v-img");
  const _component_v_avatar = _resolveComponent("v-avatar");
  const _component_v_list_item_subtitle = _resolveComponent("v-list-item-subtitle");
  const _component_v_progress_linear = _resolveComponent("v-progress-linear");
  const _component_v_col = _resolveComponent("v-col");
  const _component_v_row = _resolveComponent("v-row");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_card_actions = _resolveComponent("v-card-actions");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_switch = _resolveComponent("v-switch");
  const _component_v_spacer = _resolveComponent("v-spacer");
  const _component_v_dialog = _resolveComponent("v-dialog");
  const _component_v_progress_circular = _resolveComponent("v-progress-circular");

  return (_openBlock(), _createElementBlock(_Fragment, null, [
    _createElementVNode("div", _hoisted_1, [
      _createVNode(_component_v_card, {
        flat: "",
        class: "rounded border",
        style: {"display":"flex","flex-direction":"column","max-height":"85vh"}
      }, {
        default: _withCtx(() => [
          _createVNode(_component_v_card_title, { class: "text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-gradient" }, {
            default: _withCtx(() => [
              _createVNode(_component_v_icon, {
                icon: "mdi-file-link",
                class: "mr-2",
                color: "primary",
                size: "small"
              }),
              _cache[14] || (_cache[14] = _createElementVNode("span", null, "115网盘STRM助手", -1))
            ]),
            _: 1
          }),
          _createVNode(_component_v_card_text, {
            class: "px-3 py-1",
            style: {"flex-grow":"1","overflow-y":"auto","padding-bottom":"48px"}
          }, {
            default: _withCtx(() => [
              (error.value)
                ? (_openBlock(), _createBlock(_component_v_alert, {
                    key: 0,
                    type: "error",
                    density: "compact",
                    class: "mb-2",
                    variant: "tonal",
                    closable: ""
                  }, {
                    default: _withCtx(() => [
                      _createTextVNode(_toDisplayString(error.value), 1)
                    ]),
                    _: 1
                  }))
                : _createCommentVNode("", true),
              (actionMessage.value)
                ? (_openBlock(), _createBlock(_component_v_alert, {
                    key: 1,
                    type: actionMessageType.value,
                    density: "compact",
                    class: "mb-2",
                    variant: "tonal",
                    closable: ""
                  }, {
                    default: _withCtx(() => [
                      _createTextVNode(_toDisplayString(actionMessage.value), 1)
                    ]),
                    _: 1
                  }, 8, ["type"]))
                : _createCommentVNode("", true),
              (loading.value && !initialDataLoaded.value)
                ? (_openBlock(), _createBlock(_component_v_skeleton_loader, {
                    key: 2,
                    type: "article, actions"
                  }))
                : _createCommentVNode("", true),
              (initialDataLoaded.value)
                ? (_openBlock(), _createElementBlock("div", _hoisted_2, [
                    _createVNode(_component_v_row, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_card, {
                              flat: "",
                              class: "rounded mb-3 border config-card"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_card_title, { class: "text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient" }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_icon, {
                                      icon: "mdi-information",
                                      class: "mr-2",
                                      color: "primary",
                                      size: "small"
                                    }),
                                    _cache[15] || (_cache[15] = _createElementVNode("span", null, "系统状态", -1))
                                  ]),
                                  _: 1
                                }),
                                _createVNode(_component_v_card_text, { class: "pa-0" }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list, { class: "bg-transparent pa-0" }, {
                                      default: _withCtx(() => [
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: status.enabled ? 'success' : 'grey',
                                              icon: "mdi-power",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: status.enabled ? 'success' : 'grey',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(status.enabled ? '已启用' : '已禁用'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[16] || (_cache[16] = [
                                                _createTextVNode("插件状态")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" }),
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: status.has_client && __props.initialConfig?.cookies ? 'success' : 'error',
                                              icon: "mdi-account-check",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: status.has_client && __props.initialConfig?.cookies ? 'success' : 'error',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(status.has_client && __props.initialConfig?.cookies ? '已连接' : '未连接'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[17] || (_cache[17] = [
                                                _createTextVNode("115客户端状态")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" }),
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: status.running ? 'warning' : 'success',
                                              icon: "mdi-play-circle",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: status.running ? 'warning' : 'success',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(status.running ? '运行中' : '空闲'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[18] || (_cache[18] = [
                                                _createTextVNode("任务状态")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        })
                                      ]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                })
                              ]),
                              _: 1
                            }),
                            _createVNode(_component_v_card, {
                              flat: "",
                              class: "rounded mb-3 border config-card"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_card_title, { class: "text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient" }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_icon, {
                                      icon: "mdi-account-box",
                                      class: "mr-2",
                                      color: "primary",
                                      size: "small"
                                    }),
                                    _cache[19] || (_cache[19] = _createElementVNode("span", null, "115账户信息", -1))
                                  ]),
                                  _: 1
                                }),
                                _createVNode(_component_v_card_text, { class: "pa-0" }, {
                                  default: _withCtx(() => [
                                    (userInfo.loading || storageInfo.loading)
                                      ? (_openBlock(), _createBlock(_component_v_skeleton_loader, {
                                          key: 0,
                                          type: "list-item-avatar-three-line, list-item-three-line"
                                        }))
                                      : (_openBlock(), _createElementBlock("div", _hoisted_3, [
                                          (userInfo.error || storageInfo.error)
                                            ? (_openBlock(), _createBlock(_component_v_alert, {
                                                key: 0,
                                                type: "warning",
                                                density: "compact",
                                                class: "ma-2",
                                                variant: "tonal"
                                              }, {
                                                default: _withCtx(() => [
                                                  _createTextVNode(_toDisplayString(userInfo.error || storageInfo.error), 1)
                                                ]),
                                                _: 1
                                              }))
                                            : (_openBlock(), _createBlock(_component_v_list, {
                                                key: 1,
                                                class: "bg-transparent pa-0"
                                              }, {
                                                default: _withCtx(() => [
                                                  _createVNode(_component_v_list_item, { class: "px-3 py-1" }, {
                                                    prepend: _withCtx(() => [
                                                      _createVNode(_component_v_avatar, {
                                                        size: "32",
                                                        class: "mr-2"
                                                      }, {
                                                        default: _withCtx(() => [
                                                          (userInfo.avatar)
                                                            ? (_openBlock(), _createBlock(_component_v_img, {
                                                                key: 0,
                                                                src: userInfo.avatar,
                                                                alt: userInfo.name
                                                              }, null, 8, ["src", "alt"]))
                                                            : (_openBlock(), _createBlock(_component_v_icon, {
                                                                key: 1,
                                                                icon: "mdi-account-circle"
                                                              }))
                                                        ]),
                                                        _: 1
                                                      })
                                                    ]),
                                                    default: _withCtx(() => [
                                                      _createVNode(_component_v_list_item_title, { class: "text-body-1 font-weight-medium" }, {
                                                        default: _withCtx(() => [
                                                          _createTextVNode(_toDisplayString(userInfo.name || '未知用户'), 1)
                                                        ]),
                                                        _: 1
                                                      })
                                                    ]),
                                                    _: 1
                                                  }),
                                                  _createVNode(_component_v_divider, { class: "my-0" }),
                                                  _createVNode(_component_v_list_item, { class: "px-3 py-1" }, {
                                                    prepend: _withCtx(() => [
                                                      _createVNode(_component_v_icon, {
                                                        color: userInfo.is_vip ? 'amber-darken-2' : 'grey',
                                                        icon: "mdi-shield-crown",
                                                        size: "small"
                                                      }, null, 8, ["color"])
                                                    ]),
                                                    append: _withCtx(() => [
                                                      _createVNode(_component_v_chip, {
                                                        color: userInfo.is_vip ? 'success' : 'grey',
                                                        size: "x-small",
                                                        variant: "tonal"
                                                      }, {
                                                        default: _withCtx(() => [
                                                          _createTextVNode(_toDisplayString(userInfo.is_vip ? (userInfo.is_forever_vip ? '永久VIP' : `VIP (至 ${userInfo.vip_expire_date
                              || 'N/A'})`) : '非VIP'), 1)
                                                        ]),
                                                        _: 1
                                                      }, 8, ["color"])
                                                    ]),
                                                    default: _withCtx(() => [
                                                      _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                                        default: _withCtx(() => _cache[20] || (_cache[20] = [
                                                          _createTextVNode("VIP状态")
                                                        ])),
                                                        _: 1
                                                      })
                                                    ]),
                                                    _: 1
                                                  }),
                                                  _createVNode(_component_v_divider, { class: "my-0" }),
                                                  _createVNode(_component_v_list_item, { class: "px-3 py-1" }, {
                                                    default: _withCtx(() => [
                                                      _createVNode(_component_v_list_item_title, { class: "text-body-2 mb-1" }, {
                                                        default: _withCtx(() => _cache[21] || (_cache[21] = [
                                                          _createTextVNode("存储空间")
                                                        ])),
                                                        _: 1
                                                      }),
                                                      (storageInfo.used && storageInfo.total)
                                                        ? (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                            key: 0,
                                                            class: "text-caption"
                                                          }, {
                                                            default: _withCtx(() => [
                                                              _createTextVNode(" 已用 " + _toDisplayString(storageInfo.used) + " / 总共 " + _toDisplayString(storageInfo.total) + " (剩余 " + _toDisplayString(storageInfo.remaining) + ") ", 1)
                                                            ]),
                                                            _: 1
                                                          }))
                                                        : _createCommentVNode("", true),
                                                      (storageInfo.used && storageInfo.total)
                                                        ? (_openBlock(), _createBlock(_component_v_progress_linear, {
                                                            key: 1,
                                                            "model-value": calculateStoragePercentage(storageInfo.used, storageInfo.total),
                                                            color: "primary",
                                                            height: "6",
                                                            rounded: "",
                                                            class: "mt-1"
                                                          }, null, 8, ["model-value"]))
                                                        : (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                            key: 2,
                                                            class: "text-caption text-grey"
                                                          }, {
                                                            default: _withCtx(() => _cache[22] || (_cache[22] = [
                                                              _createTextVNode(" 存储信息不可用 ")
                                                            ])),
                                                            _: 1
                                                          }))
                                                    ]),
                                                    _: 1
                                                  })
                                                ]),
                                                _: 1
                                              }))
                                        ]))
                                  ]),
                                  _: 1
                                })
                              ]),
                              _: 1
                            }),
                            _createVNode(_component_v_card, {
                              flat: "",
                              class: "rounded mb-3 border config-card"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_card_title, { class: "text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient" }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_icon, {
                                      icon: "mdi-puzzle",
                                      class: "mr-2",
                                      color: "primary",
                                      size: "small"
                                    }),
                                    _cache[23] || (_cache[23] = _createElementVNode("span", null, "功能配置", -1))
                                  ]),
                                  _: 1
                                }),
                                _createVNode(_component_v_card_text, { class: "pa-0" }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list, { class: "bg-transparent pa-0" }, {
                                      default: _withCtx(() => [
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: __props.initialConfig?.transfer_monitor_enabled ? 'success' : 'grey',
                                              icon: "mdi-file-move",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: __props.initialConfig?.transfer_monitor_enabled ? 'success' : 'grey',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(__props.initialConfig?.transfer_monitor_enabled ? '已启用' : '已禁用'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[24] || (_cache[24] = [
                                                _createTextVNode("监控MP整理")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" }),
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: __props.initialConfig?.timing_full_sync_strm ? 'success' : 'grey',
                                              icon: "mdi-sync",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: __props.initialConfig?.timing_full_sync_strm ? 'success' : 'grey',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(__props.initialConfig?.timing_full_sync_strm ? '已启用' : '已禁用'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[25] || (_cache[25] = [
                                                _createTextVNode("定期全量同步")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" }),
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: __props.initialConfig?.increment_sync_strm_enabled ? 'success' : 'grey',
                                              icon: "mdi-book-sync",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: __props.initialConfig?.increment_sync_strm_enabled ? 'success' : 'grey',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(__props.initialConfig?.increment_sync_strm_enabled ? '已启用' : '已禁用'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[26] || (_cache[26] = [
                                                _createTextVNode("定期增量同步")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" }),
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: __props.initialConfig?.monitor_life_enabled ? 'success' : 'grey',
                                              icon: "mdi-calendar-heart",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: __props.initialConfig?.monitor_life_enabled ? 'success' : 'grey',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(__props.initialConfig?.monitor_life_enabled ? '已启用' : '已禁用'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[27] || (_cache[27] = [
                                                _createTextVNode("监控115生活事件")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" }),
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: __props.initialConfig?.pan_transfer_enabled ? 'success' : 'grey',
                                              icon: "mdi-transfer",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: __props.initialConfig?.pan_transfer_enabled ? 'success' : 'grey',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(__props.initialConfig?.pan_transfer_enabled ? '已启用' : '已禁用'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[28] || (_cache[28] = [
                                                _createTextVNode("网盘整理")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" }),
                                        _createVNode(_component_v_list_item, {
                                          class: "px-3 py-0",
                                          style: {"min-height":"34px"}
                                        }, {
                                          prepend: _withCtx(() => [
                                            _createVNode(_component_v_icon, {
                                              color: __props.initialConfig?.clear_recyclebin_enabled || __props.initialConfig?.clear_receive_path_enabled ? 'success' : 'grey',
                                              icon: "mdi-broom",
                                              size: "small"
                                            }, null, 8, ["color"])
                                          ]),
                                          append: _withCtx(() => [
                                            _createVNode(_component_v_chip, {
                                              color: __props.initialConfig?.clear_recyclebin_enabled || __props.initialConfig?.clear_receive_path_enabled ? 'success' : 'grey',
                                              size: "x-small",
                                              variant: "tonal"
                                            }, {
                                              default: _withCtx(() => [
                                                _createTextVNode(_toDisplayString(__props.initialConfig?.clear_recyclebin_enabled || __props.initialConfig?.clear_receive_path_enabled ?
                            '已启用' : '已禁用'), 1)
                                              ]),
                                              _: 1
                                            }, 8, ["color"])
                                          ]),
                                          default: _withCtx(() => [
                                            _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                              default: _withCtx(() => _cache[29] || (_cache[29] = [
                                                _createTextVNode("定期清理")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _: 1
                                        }),
                                        _createVNode(_component_v_divider, { class: "my-0" })
                                      ]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                })
                              ]),
                              _: 1
                            })
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_card, {
                              flat: "",
                              class: "rounded mb-3 border config-card"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_card_title, { class: "text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient" }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_icon, {
                                      icon: "mdi-folder-search",
                                      class: "mr-2",
                                      color: "primary",
                                      size: "small"
                                    }),
                                    _cache[30] || (_cache[30] = _createElementVNode("span", null, "路径配置", -1))
                                  ]),
                                  _: 1
                                }),
                                _createVNode(_component_v_card_text, { class: "pa-0" }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list, { class: "bg-transparent pa-0" }, {
                                      default: _withCtx(() => [
                                        (__props.initialConfig?.transfer_monitor_enabled)
                                          ? (_openBlock(), _createBlock(_component_v_list_item, {
                                              key: 0,
                                              class: "px-3 py-1 mb-1"
                                            }, {
                                              default: _withCtx(() => [
                                                _createVNode(_component_v_list_item_title, { class: "text-body-2 font-weight-medium" }, {
                                                  default: _withCtx(() => _cache[31] || (_cache[31] = [
                                                    _createTextVNode("监控MP整理路径")
                                                  ])),
                                                  _: 1
                                                }),
                                                (getPathsCount(__props.initialConfig?.transfer_monitor_paths) > 0)
                                                  ? (_openBlock(true), _createElementBlock(_Fragment, { key: 0 }, _renderList(getParsedPaths(__props.initialConfig?.transfer_monitor_paths), (path, index) => {
                                                      return (_openBlock(), _createElementBlock(_Fragment, {
                                                        key: `transfer-${index}`
                                                      }, [
                                                        (index > 0)
                                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                                              key: 0,
                                                              class: "my-1"
                                                            }))
                                                          : _createCommentVNode("", true),
                                                        _createVNode(_component_v_list_item_subtitle, { class: "text-caption pa-0 pt-1" }, {
                                                          default: _withCtx(() => [
                                                            _createElementVNode("div", _hoisted_4, [
                                                              _createVNode(_component_v_row, {
                                                                dense: "",
                                                                align: "center"
                                                              }, {
                                                                default: _withCtx(() => [
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "primary",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[32] || (_cache[32] = [
                                                                          _createTextVNode("mdi-folder-home")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.local
                                                                      }, [
                                                                        _cache[33] || (_cache[33] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "本地目录", -1)),
                                                                        _createElementVNode("span", _hoisted_6, _toDisplayString(path.local || '-'), 1)
                                                                      ], 8, _hoisted_5)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "2",
                                                                    class: "text-center my-1 my-sm-0"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        color: "primary",
                                                                        class: "icon-spin-animation"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[34] || (_cache[34] = [
                                                                          _createTextVNode("mdi-sync")
                                                                        ])),
                                                                        _: 1
                                                                      })
                                                                    ]),
                                                                    _: 1
                                                                  }),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "success",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[35] || (_cache[35] = [
                                                                          _createTextVNode("mdi-cloud")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.remote
                                                                      }, [
                                                                        _cache[36] || (_cache[36] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "网盘目录", -1)),
                                                                        _createElementVNode("span", _hoisted_8, _toDisplayString(path.remote || '-'), 1)
                                                                      ], 8, _hoisted_7)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024)
                                                                ]),
                                                                _: 2
                                                              }, 1024)
                                                            ])
                                                          ]),
                                                          _: 2
                                                        }, 1024)
                                                      ], 64))
                                                    }), 128))
                                                  : (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                      key: 1,
                                                      class: "text-caption text-error mt-1"
                                                    }, {
                                                      default: _withCtx(() => _cache[37] || (_cache[37] = [
                                                        _createTextVNode("未配置路径")
                                                      ])),
                                                      _: 1
                                                    }))
                                              ]),
                                              _: 1
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.transfer_monitor_enabled && (__props.initialConfig?.increment_sync_strm_enabled || __props.initialConfig?.timing_full_sync_strm || __props.initialConfig?.monitor_life_enabled || __props.initialConfig?.pan_transfer_enabled || __props.initialConfig?.directory_upload_enabled))
                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                              key: 1,
                                              class: "my-0"
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.timing_full_sync_strm)
                                          ? (_openBlock(), _createBlock(_component_v_list_item, {
                                              key: 2,
                                              class: "px-3 py-1 mb-1"
                                            }, {
                                              default: _withCtx(() => [
                                                _createVNode(_component_v_list_item_title, { class: "text-body-2 font-weight-medium" }, {
                                                  default: _withCtx(() => _cache[38] || (_cache[38] = [
                                                    _createTextVNode("全量同步路径")
                                                  ])),
                                                  _: 1
                                                }),
                                                (getPathsCount(__props.initialConfig?.full_sync_strm_paths) > 0)
                                                  ? (_openBlock(true), _createElementBlock(_Fragment, { key: 0 }, _renderList(getParsedPaths(__props.initialConfig?.full_sync_strm_paths), (path, index) => {
                                                      return (_openBlock(), _createElementBlock(_Fragment, {
                                                        key: `fullsync-${index}`
                                                      }, [
                                                        (index > 0)
                                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                                              key: 0,
                                                              class: "my-1"
                                                            }))
                                                          : _createCommentVNode("", true),
                                                        _createVNode(_component_v_list_item_subtitle, { class: "text-caption pa-0 pt-1" }, {
                                                          default: _withCtx(() => [
                                                            _createElementVNode("div", _hoisted_9, [
                                                              _createVNode(_component_v_row, {
                                                                dense: "",
                                                                align: "center"
                                                              }, {
                                                                default: _withCtx(() => [
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "primary",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[39] || (_cache[39] = [
                                                                          _createTextVNode("mdi-folder-home")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.local
                                                                      }, [
                                                                        _cache[40] || (_cache[40] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "本地目录", -1)),
                                                                        _createElementVNode("span", _hoisted_11, _toDisplayString(path.local || '-'), 1)
                                                                      ], 8, _hoisted_10)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "2",
                                                                    class: "text-center my-1 my-sm-0"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        color: "primary",
                                                                        class: "icon-spin-animation"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[41] || (_cache[41] = [
                                                                          _createTextVNode("mdi-sync")
                                                                        ])),
                                                                        _: 1
                                                                      })
                                                                    ]),
                                                                    _: 1
                                                                  }),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "success",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[42] || (_cache[42] = [
                                                                          _createTextVNode("mdi-cloud")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.remote
                                                                      }, [
                                                                        _cache[43] || (_cache[43] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "网盘目录", -1)),
                                                                        _createElementVNode("span", _hoisted_13, _toDisplayString(path.remote || '-'), 1)
                                                                      ], 8, _hoisted_12)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024)
                                                                ]),
                                                                _: 2
                                                              }, 1024)
                                                            ])
                                                          ]),
                                                          _: 2
                                                        }, 1024)
                                                      ], 64))
                                                    }), 128))
                                                  : (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                      key: 1,
                                                      class: "text-caption text-error mt-1"
                                                    }, {
                                                      default: _withCtx(() => _cache[44] || (_cache[44] = [
                                                        _createTextVNode("未配置路径")
                                                      ])),
                                                      _: 1
                                                    }))
                                              ]),
                                              _: 1
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.timing_full_sync_strm && (__props.initialConfig?.increment_sync_strm_enabled || __props.initialConfig?.monitor_life_enabled || __props.initialConfig?.pan_transfer_enabled || __props.initialConfig?.directory_upload_enabled))
                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                              key: 3,
                                              class: "my-0"
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.increment_sync_strm_enabled)
                                          ? (_openBlock(), _createBlock(_component_v_list_item, {
                                              key: 4,
                                              class: "px-3 py-1 mb-1"
                                            }, {
                                              default: _withCtx(() => [
                                                _createVNode(_component_v_list_item_title, { class: "text-body-2 font-weight-medium" }, {
                                                  default: _withCtx(() => _cache[45] || (_cache[45] = [
                                                    _createTextVNode("增量同步路径")
                                                  ])),
                                                  _: 1
                                                }),
                                                (getPathsCount(__props.initialConfig?.increment_sync_strm_paths) > 0)
                                                  ? (_openBlock(true), _createElementBlock(_Fragment, { key: 0 }, _renderList(getParsedPaths(__props.initialConfig?.increment_sync_strm_paths), (path, index) => {
                                                      return (_openBlock(), _createElementBlock(_Fragment, {
                                                        key: `fullsync-${index}`
                                                      }, [
                                                        (index > 0)
                                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                                              key: 0,
                                                              class: "my-1"
                                                            }))
                                                          : _createCommentVNode("", true),
                                                        _createVNode(_component_v_list_item_subtitle, { class: "text-caption pa-0 pt-1" }, {
                                                          default: _withCtx(() => [
                                                            _createElementVNode("div", _hoisted_14, [
                                                              _createVNode(_component_v_row, {
                                                                dense: "",
                                                                align: "center"
                                                              }, {
                                                                default: _withCtx(() => [
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "primary",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[46] || (_cache[46] = [
                                                                          _createTextVNode("mdi-folder-home")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.local
                                                                      }, [
                                                                        _cache[47] || (_cache[47] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "本地目录", -1)),
                                                                        _createElementVNode("span", _hoisted_16, _toDisplayString(path.local || '-'), 1)
                                                                      ], 8, _hoisted_15)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "2",
                                                                    class: "text-center my-1 my-sm-0"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        color: "primary",
                                                                        class: "icon-spin-animation"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[48] || (_cache[48] = [
                                                                          _createTextVNode("mdi-sync")
                                                                        ])),
                                                                        _: 1
                                                                      })
                                                                    ]),
                                                                    _: 1
                                                                  }),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "success",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[49] || (_cache[49] = [
                                                                          _createTextVNode("mdi-cloud")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.remote
                                                                      }, [
                                                                        _cache[50] || (_cache[50] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "网盘目录", -1)),
                                                                        _createElementVNode("span", _hoisted_18, _toDisplayString(path.remote || '-'), 1)
                                                                      ], 8, _hoisted_17)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024)
                                                                ]),
                                                                _: 2
                                                              }, 1024)
                                                            ])
                                                          ]),
                                                          _: 2
                                                        }, 1024)
                                                      ], 64))
                                                    }), 128))
                                                  : (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                      key: 1,
                                                      class: "text-caption text-error mt-1"
                                                    }, {
                                                      default: _withCtx(() => _cache[51] || (_cache[51] = [
                                                        _createTextVNode("未配置路径")
                                                      ])),
                                                      _: 1
                                                    }))
                                              ]),
                                              _: 1
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.timing_full_sync_strm && (__props.initialConfig?.monitor_life_enabled || __props.initialConfig?.pan_transfer_enabled || __props.initialConfig?.directory_upload_enabled))
                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                              key: 5,
                                              class: "my-0"
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.monitor_life_enabled)
                                          ? (_openBlock(), _createBlock(_component_v_list_item, {
                                              key: 6,
                                              class: "px-3 py-1 mb-1"
                                            }, {
                                              default: _withCtx(() => [
                                                _createVNode(_component_v_list_item_title, { class: "text-body-2 font-weight-medium" }, {
                                                  default: _withCtx(() => _cache[52] || (_cache[52] = [
                                                    _createTextVNode("监控115生活事件路径")
                                                  ])),
                                                  _: 1
                                                }),
                                                (getPathsCount(__props.initialConfig?.monitor_life_paths) > 0)
                                                  ? (_openBlock(true), _createElementBlock(_Fragment, { key: 0 }, _renderList(getParsedPaths(__props.initialConfig?.monitor_life_paths), (path, index) => {
                                                      return (_openBlock(), _createElementBlock(_Fragment, {
                                                        key: `life-${index}`
                                                      }, [
                                                        (index > 0)
                                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                                              key: 0,
                                                              class: "my-1"
                                                            }))
                                                          : _createCommentVNode("", true),
                                                        _createVNode(_component_v_list_item_subtitle, { class: "text-caption pa-0 pt-1" }, {
                                                          default: _withCtx(() => [
                                                            _createElementVNode("div", _hoisted_19, [
                                                              _createVNode(_component_v_row, {
                                                                dense: "",
                                                                align: "center"
                                                              }, {
                                                                default: _withCtx(() => [
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "primary",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[53] || (_cache[53] = [
                                                                          _createTextVNode("mdi-folder-home")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.local
                                                                      }, [
                                                                        _cache[54] || (_cache[54] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "本地目录", -1)),
                                                                        _createElementVNode("span", _hoisted_21, _toDisplayString(path.local || '-'), 1)
                                                                      ], 8, _hoisted_20)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "2",
                                                                    class: "text-center my-1 my-sm-0"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        color: "primary",
                                                                        class: "icon-spin-animation"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[55] || (_cache[55] = [
                                                                          _createTextVNode("mdi-sync")
                                                                        ])),
                                                                        _: 1
                                                                      })
                                                                    ]),
                                                                    _: 1
                                                                  }),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    sm: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "success",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[56] || (_cache[56] = [
                                                                          _createTextVNode("mdi-cloud")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: path.remote
                                                                      }, [
                                                                        _cache[57] || (_cache[57] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "网盘目录", -1)),
                                                                        _createElementVNode("span", _hoisted_23, _toDisplayString(path.remote || '-'), 1)
                                                                      ], 8, _hoisted_22)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024)
                                                                ]),
                                                                _: 2
                                                              }, 1024)
                                                            ])
                                                          ]),
                                                          _: 2
                                                        }, 1024)
                                                      ], 64))
                                                    }), 128))
                                                  : (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                      key: 1,
                                                      class: "text-caption text-error mt-1"
                                                    }, {
                                                      default: _withCtx(() => _cache[58] || (_cache[58] = [
                                                        _createTextVNode("未配置路径")
                                                      ])),
                                                      _: 1
                                                    }))
                                              ]),
                                              _: 1
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.monitor_life_enabled && (__props.initialConfig?.pan_transfer_enabled || __props.initialConfig?.directory_upload_enabled))
                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                              key: 7,
                                              class: "my-0"
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.pan_transfer_enabled)
                                          ? (_openBlock(), _createBlock(_component_v_list_item, {
                                              key: 8,
                                              class: "px-3 py-1"
                                            }, {
                                              default: _withCtx(() => [
                                                _createVNode(_component_v_list_item_title, { class: "text-body-2 font-weight-medium" }, {
                                                  default: _withCtx(() => _cache[59] || (_cache[59] = [
                                                    _createTextVNode("网盘整理目录")
                                                  ])),
                                                  _: 1
                                                }),
                                                (getPanTransferPathsCount(__props.initialConfig?.pan_transfer_paths) > 0)
                                                  ? (_openBlock(true), _createElementBlock(_Fragment, { key: 0 }, _renderList(getParsedPanTransferPaths(__props.initialConfig?.pan_transfer_paths), (pathItem, index) => {
                                                      return (_openBlock(), _createElementBlock(_Fragment, {
                                                        key: `pan-${index}`
                                                      }, [
                                                        (index > 0)
                                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                                              key: 0,
                                                              class: "my-1"
                                                            }))
                                                          : _createCommentVNode("", true),
                                                        _createVNode(_component_v_list_item_subtitle, { class: "text-caption pa-0 pt-1" }, {
                                                          default: _withCtx(() => [
                                                            _createElementVNode("div", _hoisted_24, [
                                                              _createElementVNode("div", _hoisted_25, [
                                                                _createVNode(_component_v_icon, {
                                                                  size: "small",
                                                                  color: "success",
                                                                  class: "mr-2 flex-shrink-0"
                                                                }, {
                                                                  default: _withCtx(() => _cache[60] || (_cache[60] = [
                                                                    _createTextVNode("mdi-folder-arrow-down")
                                                                  ])),
                                                                  _: 1
                                                                }),
                                                                _createElementVNode("div", {
                                                                  class: "text-truncate w-100",
                                                                  title: pathItem.path
                                                                }, [
                                                                  _cache[61] || (_cache[61] = _createElementVNode("span", {
                                                                    class: "text-caption font-weight-medium d-block",
                                                                    style: {"line-height":"1.2"}
                                                                  }, "待整理网盘目录", -1)),
                                                                  _createElementVNode("span", _hoisted_27, _toDisplayString(pathItem.path), 1)
                                                                ], 8, _hoisted_26)
                                                              ])
                                                            ])
                                                          ]),
                                                          _: 2
                                                        }, 1024)
                                                      ], 64))
                                                    }), 128))
                                                  : (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                      key: 1,
                                                      class: "text-caption text-error mt-1"
                                                    }, {
                                                      default: _withCtx(() => _cache[62] || (_cache[62] = [
                                                        _createTextVNode("未配置网盘整理目录")
                                                      ])),
                                                      _: 1
                                                    }))
                                              ]),
                                              _: 1
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.pan_transfer_enabled && __props.initialConfig?.directory_upload_enabled)
                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                              key: 9,
                                              class: "my-0"
                                            }))
                                          : _createCommentVNode("", true),
                                        (__props.initialConfig?.directory_upload_enabled)
                                          ? (_openBlock(), _createBlock(_component_v_list_item, {
                                              key: 10,
                                              class: "px-3 py-1"
                                            }, {
                                              default: _withCtx(() => [
                                                _createVNode(_component_v_list_item_title, { class: "text-body-2 font-weight-medium" }, {
                                                  default: _withCtx(() => _cache[63] || (_cache[63] = [
                                                    _createTextVNode("目录上传路径")
                                                  ])),
                                                  _: 1
                                                }),
                                                (__props.initialConfig?.directory_upload_path && __props.initialConfig.directory_upload_path.length > 0)
                                                  ? (_openBlock(true), _createElementBlock(_Fragment, { key: 0 }, _renderList(__props.initialConfig.directory_upload_path, (pathGroup, groupIndex) => {
                                                      return (_openBlock(), _createElementBlock(_Fragment, {
                                                        key: `upload-group-${groupIndex}`
                                                      }, [
                                                        (groupIndex > 0)
                                                          ? (_openBlock(), _createBlock(_component_v_divider, {
                                                              key: 0,
                                                              class: "my-1"
                                                            }))
                                                          : _createCommentVNode("", true),
                                                        _createVNode(_component_v_list_item_subtitle, { class: "text-caption pa-0 pt-1" }, {
                                                          default: _withCtx(() => [
                                                            _createElementVNode("div", _hoisted_28, [
                                                              _createVNode(_component_v_row, {
                                                                dense: "",
                                                                align: "center"
                                                              }, {
                                                                default: _withCtx(() => [
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    md: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "primary",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[64] || (_cache[64] = [
                                                                          _createTextVNode("mdi-folder-table")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: pathGroup.src
                                                                      }, [
                                                                        _cache[65] || (_cache[65] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "本地监控", -1)),
                                                                        _createElementVNode("span", _hoisted_30, _toDisplayString(pathGroup.src || '-'), 1)
                                                                      ], 8, _hoisted_29)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    md: "2",
                                                                    class: "text-center my-1 my-md-0"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        color: "primary",
                                                                        class: "icon-spin-animation"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[66] || (_cache[66] = [
                                                                          _createTextVNode("mdi-sync")
                                                                        ])),
                                                                        _: 1
                                                                      })
                                                                    ]),
                                                                    _: 1
                                                                  }),
                                                                  _createVNode(_component_v_col, {
                                                                    cols: "12",
                                                                    md: "5",
                                                                    class: "d-flex align-center"
                                                                  }, {
                                                                    default: _withCtx(() => [
                                                                      _createVNode(_component_v_icon, {
                                                                        size: "small",
                                                                        color: "success",
                                                                        class: "mr-2 flex-shrink-0"
                                                                      }, {
                                                                        default: _withCtx(() => _cache[67] || (_cache[67] = [
                                                                          _createTextVNode("mdi-cloud-upload")
                                                                        ])),
                                                                        _: 1
                                                                      }),
                                                                      _createElementVNode("div", {
                                                                        class: "text-truncate w-100",
                                                                        title: pathGroup.dest_remote
                                                                      }, [
                                                                        _cache[68] || (_cache[68] = _createElementVNode("span", {
                                                                          class: "text-caption font-weight-medium d-block",
                                                                          style: {"line-height":"1.2"}
                                                                        }, "网盘上传", -1)),
                                                                        _createElementVNode("span", _hoisted_32, _toDisplayString(pathGroup.dest_remote || '-'), 1)
                                                                      ], 8, _hoisted_31)
                                                                    ]),
                                                                    _: 2
                                                                  }, 1024)
                                                                ]),
                                                                _: 2
                                                              }, 1024),
                                                              (pathGroup.dest_local || typeof pathGroup.delete === 'boolean')
                                                                ? (_openBlock(), _createElementBlock("div", _hoisted_33, [
                                                                    _createVNode(_component_v_row, {
                                                                      dense: "",
                                                                      align: "center",
                                                                      class: "mt-1"
                                                                    }, {
                                                                      default: _withCtx(() => [
                                                                        _createVNode(_component_v_col, {
                                                                          cols: "12",
                                                                          md: typeof pathGroup.delete === 'boolean' ? 7 : 12
                                                                        }, {
                                                                          default: _withCtx(() => [
                                                                            (pathGroup.dest_local)
                                                                              ? (_openBlock(), _createElementBlock("div", _hoisted_34, [
                                                                                  _createVNode(_component_v_icon, {
                                                                                    size: "small",
                                                                                    color: "warning",
                                                                                    class: "mr-2 flex-shrink-0"
                                                                                  }, {
                                                                                    default: _withCtx(() => _cache[69] || (_cache[69] = [
                                                                                      _createTextVNode("mdi-content-copy")
                                                                                    ])),
                                                                                    _: 1
                                                                                  }),
                                                                                  _createElementVNode("div", {
                                                                                    class: "text-truncate w-100",
                                                                                    title: pathGroup.dest_local
                                                                                  }, [
                                                                                    _cache[70] || (_cache[70] = _createElementVNode("span", {
                                                                                      class: "text-caption font-weight-medium d-block",
                                                                                      style: {"line-height":"1.2"}
                                                                                    }, "本地复制", -1)),
                                                                                    _createElementVNode("span", _hoisted_36, _toDisplayString(pathGroup.dest_local), 1)
                                                                                  ], 8, _hoisted_35)
                                                                                ]))
                                                                              : _createCommentVNode("", true)
                                                                          ]),
                                                                          _: 2
                                                                        }, 1032, ["md"]),
                                                                        (typeof pathGroup.delete === 'boolean')
                                                                          ? (_openBlock(), _createBlock(_component_v_col, {
                                                                              key: 0,
                                                                              cols: "12",
                                                                              md: pathGroup.dest_local ? 5 : 12,
                                                                              class: _normalizeClass(["d-flex align-center", { 'justify-md-end': pathGroup.dest_local, 'mt-1 mt-md-0': pathGroup.dest_local }])
                                                                            }, {
                                                                              default: _withCtx(() => [
                                                                                _createVNode(_component_v_icon, {
                                                                                  size: "small",
                                                                                  color: pathGroup.delete ? 'error' : 'grey-darken-1',
                                                                                  class: "mr-1 flex-shrink-0"
                                                                                }, {
                                                                                  default: _withCtx(() => [
                                                                                    _createTextVNode(_toDisplayString(pathGroup.delete ? 'mdi-delete' : 'mdi-delete-off'), 1)
                                                                                  ]),
                                                                                  _: 2
                                                                                }, 1032, ["color"]),
                                                                                _createElementVNode("span", {
                                                                                  class: _normalizeClass(["text-caption", pathGroup.delete ? 'text-error' : 'text-grey-darken-1'])
                                                                                }, _toDisplayString(pathGroup.delete ? '删除源' : '不删源'), 3)
                                                                              ]),
                                                                              _: 2
                                                                            }, 1032, ["md", "class"]))
                                                                          : _createCommentVNode("", true)
                                                                      ]),
                                                                      _: 2
                                                                    }, 1024)
                                                                  ]))
                                                                : _createCommentVNode("", true)
                                                            ])
                                                          ]),
                                                          _: 2
                                                        }, 1024)
                                                      ], 64))
                                                    }), 128))
                                                  : (_openBlock(), _createBlock(_component_v_list_item_subtitle, {
                                                      key: 1,
                                                      class: "text-caption text-error mt-1"
                                                    }, {
                                                      default: _withCtx(() => _cache[71] || (_cache[71] = [
                                                        _createTextVNode("未配置路径")
                                                      ])),
                                                      _: 1
                                                    }))
                                              ]),
                                              _: 1
                                            }))
                                          : _createCommentVNode("", true)
                                      ]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                })
                              ]),
                              _: 1
                            }),
                            (!status.has_client || !__props.initialConfig.cookies)
                              ? (_openBlock(), _createBlock(_component_v_card, {
                                  key: 0,
                                  flat: "",
                                  class: "rounded mb-3 border config-card"
                                }, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_card_text, { class: "pa-3" }, {
                                      default: _withCtx(() => [
                                        _createElementVNode("div", _hoisted_37, [
                                          _createVNode(_component_v_icon, {
                                            icon: "mdi-alert-circle",
                                            color: "error",
                                            class: "mr-2",
                                            size: "small"
                                          }),
                                          _createElementVNode("div", _hoisted_38, [
                                            _cache[74] || (_cache[74] = _createElementVNode("p", { class: "mb-1" }, [
                                              _createElementVNode("strong", null, "未配置115 Cookie或Cookie无效")
                                            ], -1)),
                                            _cache[75] || (_cache[75] = _createElementVNode("p", { class: "mb-0" }, "请在配置页面中设置有效的115网盘Cookie，可通过扫码登录获取。", -1)),
                                            _createVNode(_component_v_btn, {
                                              color: "primary",
                                              variant: "text",
                                              size: "small",
                                              class: "mt-1 px-2 py-0",
                                              onClick: _cache[0] || (_cache[0] = $event => (emit('switch')))
                                            }, {
                                              default: _withCtx(() => [
                                                _createVNode(_component_v_icon, {
                                                  size: "small",
                                                  class: "mr-1"
                                                }, {
                                                  default: _withCtx(() => _cache[72] || (_cache[72] = [
                                                    _createTextVNode("mdi-cog")
                                                  ])),
                                                  _: 1
                                                }),
                                                _cache[73] || (_cache[73] = _createTextVNode("前往配置 "))
                                              ]),
                                              _: 1
                                            })
                                          ])
                                        ])
                                      ]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                }))
                              : (!isProperlyCongifured.value)
                                ? (_openBlock(), _createBlock(_component_v_card, {
                                    key: 1,
                                    flat: "",
                                    class: "rounded mb-3 border config-card"
                                  }, {
                                    default: _withCtx(() => [
                                      _createVNode(_component_v_card_text, { class: "pa-3" }, {
                                        default: _withCtx(() => [
                                          _createElementVNode("div", _hoisted_39, [
                                            _createVNode(_component_v_icon, {
                                              icon: "mdi-alert-circle",
                                              color: "warning",
                                              class: "mr-2",
                                              size: "small"
                                            }),
                                            _createElementVNode("div", _hoisted_40, [
                                              _cache[78] || (_cache[78] = _createElementVNode("p", { class: "mb-1" }, [
                                                _createElementVNode("strong", null, "路径配置不完整")
                                              ], -1)),
                                              _cache[79] || (_cache[79] = _createElementVNode("p", { class: "mb-0" }, "您已配置115 Cookie，但部分功能路径未配置。请前往配置页面完善路径设置。", -1)),
                                              _createVNode(_component_v_btn, {
                                                color: "primary",
                                                variant: "text",
                                                size: "small",
                                                class: "mt-1 px-2 py-0",
                                                onClick: _cache[1] || (_cache[1] = $event => (emit('switch')))
                                              }, {
                                                default: _withCtx(() => [
                                                  _createVNode(_component_v_icon, {
                                                    size: "small",
                                                    class: "mr-1"
                                                  }, {
                                                    default: _withCtx(() => _cache[76] || (_cache[76] = [
                                                      _createTextVNode("mdi-cog")
                                                    ])),
                                                    _: 1
                                                  }),
                                                  _cache[77] || (_cache[77] = _createTextVNode("前往配置 "))
                                                ]),
                                                _: 1
                                              })
                                            ])
                                          ])
                                        ]),
                                        _: 1
                                      })
                                    ]),
                                    _: 1
                                  }))
                                : _createCommentVNode("", true)
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_card, {
                      flat: "",
                      class: "rounded mb-3 border config-card"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_card_text, { class: "d-flex align-center px-3 py-1" }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_icon, {
                              icon: "mdi-information",
                              color: "info",
                              class: "mr-2",
                              size: "small"
                            }),
                            _cache[80] || (_cache[80] = _createElementVNode("span", { class: "text-body-2" }, " 点击\"配置\"按钮进行设置，\"全量同步\"和\"分享同步\"按钮可立即执行相应任务。 ", -1))
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    })
                  ]))
                : _createCommentVNode("", true)
            ]),
            _: 1
          }),
          _createVNode(_component_v_divider),
          _createVNode(_component_v_card_actions, {
            class: "px-2 py-1 sticky-actions d-flex justify-space-between align-center",
            style: {"flex-shrink":"0"}
          }, {
            default: _withCtx(() => [
              _createVNode(_component_v_btn, {
                color: "info",
                onClick: refreshStatus,
                "prepend-icon": "mdi-refresh",
                disabled: refreshing.value,
                loading: refreshing.value,
                variant: "text",
                size: "small"
              }, {
                default: _withCtx(() => _cache[81] || (_cache[81] = [
                  _createTextVNode("刷新状态")
                ])),
                _: 1
              }, 8, ["disabled", "loading"]),
              _createElementVNode("div", _hoisted_41, [
                _createVNode(_component_v_btn, {
                  color: "warning",
                  "prepend-icon": "mdi-sync",
                  disabled: !status.enabled || !status.has_client || actionLoading.value,
                  loading: syncLoading.value,
                  onClick: triggerFullSync,
                  variant: "text",
                  size: "small",
                  class: "ml-1"
                }, {
                  default: _withCtx(() => _cache[82] || (_cache[82] = [
                    _createTextVNode(" 全量同步 ")
                  ])),
                  _: 1
                }, 8, ["disabled", "loading"]),
                _createVNode(_component_v_btn, {
                  color: "info",
                  "prepend-icon": "mdi-share-variant",
                  disabled: !status.enabled || !status.has_client || actionLoading.value,
                  loading: shareSyncLoading.value,
                  onClick: openShareDialog,
                  variant: "text",
                  size: "small",
                  class: "ml-1"
                }, {
                  default: _withCtx(() => _cache[83] || (_cache[83] = [
                    _createTextVNode(" 分享同步 ")
                  ])),
                  _: 1
                }, 8, ["disabled", "loading"]),
                _createVNode(_component_v_btn, {
                  color: "primary",
                  onClick: _cache[2] || (_cache[2] = $event => (emit('switch'))),
                  "prepend-icon": "mdi-cog",
                  variant: "text",
                  size: "small",
                  class: "ml-1"
                }, {
                  default: _withCtx(() => _cache[84] || (_cache[84] = [
                    _createTextVNode("配置")
                  ])),
                  _: 1
                }),
                _createVNode(_component_v_btn, {
                  color: "error",
                  onClick: _cache[3] || (_cache[3] = $event => (emit('close'))),
                  variant: "flat",
                  size: "small",
                  class: "ml-1 custom-close-btn",
                  "aria-label": "关闭",
                  style: {"min-width":"auto !important","padding":"0 10px !important","height":"28px !important","line-height":"28px !important"}
                }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_icon, { size: "small" }, {
                      default: _withCtx(() => _cache[85] || (_cache[85] = [
                        _createTextVNode("mdi-close")
                      ])),
                      _: 1
                    })
                  ]),
                  _: 1
                })
              ])
            ]),
            _: 1
          })
        ]),
        _: 1
      })
    ]),
    _createVNode(_component_v_dialog, {
      modelValue: shareDialog.show,
      "onUpdate:modelValue": _cache[11] || (_cache[11] = $event => ((shareDialog.show) = $event)),
      "max-width": "600"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card, null, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_title, { class: "text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_icon, {
                  icon: "mdi-share-variant",
                  class: "mr-2",
                  color: "primary",
                  size: "small"
                }),
                _cache[86] || (_cache[86] = _createElementVNode("span", null, "115网盘分享同步", -1))
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_text, { class: "px-3 py-2" }, {
              default: _withCtx(() => [
                (shareDialog.error)
                  ? (_openBlock(), _createBlock(_component_v_alert, {
                      key: 0,
                      type: "error",
                      density: "compact",
                      class: "mb-3",
                      variant: "tonal"
                    }, {
                      default: _withCtx(() => [
                        _createTextVNode(_toDisplayString(shareDialog.error), 1)
                      ]),
                      _: 1
                    }))
                  : _createCommentVNode("", true),
                _createVNode(_component_v_row, null, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_col, { cols: "12" }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_text_field, {
                          modelValue: shareDialog.shareLink,
                          "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((shareDialog.shareLink) = $event)),
                          label: "分享链接",
                          hint: "115网盘分享链接",
                          "persistent-hint": "",
                          variant: "outlined",
                          density: "compact"
                        }, null, 8, ["modelValue"])
                      ]),
                      _: 1
                    })
                  ]),
                  _: 1
                }),
                _createVNode(_component_v_row, null, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "6"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_text_field, {
                          modelValue: shareDialog.shareCode,
                          "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((shareDialog.shareCode) = $event)),
                          label: "分享码",
                          hint: "分享码，和分享链接选填一项",
                          "persistent-hint": "",
                          variant: "outlined",
                          density: "compact"
                        }, null, 8, ["modelValue"])
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "6"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_text_field, {
                          modelValue: shareDialog.receiveCode,
                          "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((shareDialog.receiveCode) = $event)),
                          label: "分享密码",
                          hint: "分享密码，如有则必填",
                          "persistent-hint": "",
                          variant: "outlined",
                          density: "compact"
                        }, null, 8, ["modelValue"])
                      ]),
                      _: 1
                    })
                  ]),
                  _: 1
                }),
                _createVNode(_component_v_row, null, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "6"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_text_field, {
                          modelValue: shareDialog.panPath,
                          "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((shareDialog.panPath) = $event)),
                          label: "分享文件夹路径",
                          hint: "分享内容列表中的相对路径，默认为根目录 /。例如，若分享链接指向一个文件夹，此路径为该文件夹内的子路径。",
                          "persistent-hint": "",
                          variant: "outlined",
                          density: "compact"
                        }, null, 8, ["modelValue"])
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "6"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_text_field, {
                          modelValue: shareDialog.localPath,
                          "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((shareDialog.localPath) = $event)),
                          label: "本地生成STRM路径",
                          hint: "本地生成STRM文件的路径",
                          "persistent-hint": "",
                          variant: "outlined",
                          density: "compact",
                          "append-icon": "mdi-folder",
                          "onClick:append": _cache[9] || (_cache[9] = $event => (openShareDirSelector('local')))
                        }, null, 8, ["modelValue"])
                      ]),
                      _: 1
                    })
                  ]),
                  _: 1
                }),
                _createVNode(_component_v_row, null, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_col, { cols: "12" }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_switch, {
                          modelValue: shareDialog.downloadMediaInfo,
                          "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((shareDialog.downloadMediaInfo) = $event)),
                          label: "下载媒体数据文件",
                          color: "primary",
                          density: "compact"
                        }, null, 8, ["modelValue"])
                      ]),
                      _: 1
                    })
                  ]),
                  _: 1
                }),
                _createVNode(_component_v_alert, {
                  type: "info",
                  variant: "tonal",
                  density: "compact",
                  class: "mt-1"
                }, {
                  default: _withCtx(() => _cache[87] || (_cache[87] = [
                    _createTextVNode(" 分享链接/分享码和分享密码 只需要二选一配置即可。"),
                    _createElementVNode("br", null, null, -1),
                    _createTextVNode(" 同时填写分享链接，分享码和分享密码时，优先读取分享链接。 ")
                  ])),
                  _: 1
                })
              ]),
              _: 1
            }),
            _createVNode(_component_v_divider),
            _createVNode(_component_v_card_actions, { class: "px-3 py-1" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_btn, {
                  color: "grey",
                  variant: "text",
                  onClick: closeShareDialog,
                  size: "small"
                }, {
                  default: _withCtx(() => _cache[88] || (_cache[88] = [
                    _createTextVNode("取消")
                  ])),
                  _: 1
                }),
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  color: "primary",
                  variant: "text",
                  onClick: executeShareSync,
                  loading: shareSyncLoading.value,
                  disabled: !isShareDialogValid.value,
                  size: "small"
                }, {
                  default: _withCtx(() => _cache[89] || (_cache[89] = [
                    _createTextVNode(" 开始同步 ")
                  ])),
                  _: 1
                }, 8, ["loading", "disabled"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }, 8, ["modelValue"]),
    _createVNode(_component_v_dialog, {
      modelValue: dirDialog.show,
      "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((dirDialog.show) = $event)),
      "max-width": "800"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card, null, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_title, { class: "text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_icon, {
                  icon: dirDialog.isLocal ? 'mdi-folder-search' : 'mdi-folder-network',
                  class: "mr-2",
                  color: "primary"
                }, null, 8, ["icon"]),
                _createElementVNode("span", null, _toDisplayString(dirDialog.isLocal ? '选择本地目录' : '选择网盘目录'), 1)
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_text, { class: "px-3 py-2" }, {
              default: _withCtx(() => [
                (dirDialog.loading)
                  ? (_openBlock(), _createElementBlock("div", _hoisted_42, [
                      _createVNode(_component_v_progress_circular, {
                        indeterminate: "",
                        color: "primary"
                      })
                    ]))
                  : (_openBlock(), _createElementBlock("div", _hoisted_43, [
                      _createVNode(_component_v_text_field, {
                        modelValue: dirDialog.currentPath,
                        "onUpdate:modelValue": _cache[12] || (_cache[12] = $event => ((dirDialog.currentPath) = $event)),
                        label: "当前路径",
                        variant: "outlined",
                        density: "compact",
                        class: "mb-2",
                        onKeyup: _withKeys(loadDirContent, ["enter"])
                      }, null, 8, ["modelValue"]),
                      _createVNode(_component_v_list, {
                        class: "border rounded",
                        "max-height": "300px",
                        "overflow-y": "auto"
                      }, {
                        default: _withCtx(() => [
                          (dirDialog.currentPath !== '/' && dirDialog.currentPath !== 'C:\\' && dirDialog.currentPath !== 'C:/')
                            ? (_openBlock(), _createBlock(_component_v_list_item, {
                                key: 0,
                                onClick: navigateToParentDir,
                                class: "py-0",
                                style: {"min-height":"auto"}
                              }, {
                                prepend: _withCtx(() => [
                                  _createVNode(_component_v_icon, {
                                    icon: "mdi-arrow-up",
                                    size: "small",
                                    class: "mr-2",
                                    color: "grey"
                                  })
                                ]),
                                default: _withCtx(() => [
                                  _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                    default: _withCtx(() => _cache[90] || (_cache[90] = [
                                      _createTextVNode("上级目录")
                                    ])),
                                    _: 1
                                  }),
                                  _createVNode(_component_v_list_item_subtitle, null, {
                                    default: _withCtx(() => _cache[91] || (_cache[91] = [
                                      _createTextVNode("..")
                                    ])),
                                    _: 1
                                  })
                                ]),
                                _: 1
                              }))
                            : _createCommentVNode("", true),
                          (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(dirDialog.items, (item, index) => {
                            return (_openBlock(), _createBlock(_component_v_list_item, {
                              key: index,
                              onClick: $event => (selectDir(item)),
                              disabled: !item.is_dir,
                              class: "py-0",
                              style: {"min-height":"auto"}
                            }, {
                              prepend: _withCtx(() => [
                                _createVNode(_component_v_icon, {
                                  icon: item.is_dir ? 'mdi-folder' : 'mdi-file',
                                  size: "small",
                                  class: "mr-2",
                                  color: item.is_dir ? 'success' : 'blue'
                                }, null, 8, ["icon", "color"])
                              ]),
                              default: _withCtx(() => [
                                _createVNode(_component_v_list_item_title, { class: "text-body-2" }, {
                                  default: _withCtx(() => [
                                    _createTextVNode(_toDisplayString(item.name), 1)
                                  ]),
                                  _: 2
                                }, 1024)
                              ]),
                              _: 2
                            }, 1032, ["onClick", "disabled"]))
                          }), 128)),
                          (!dirDialog.items.length)
                            ? (_openBlock(), _createBlock(_component_v_list_item, {
                                key: 1,
                                class: "py-2 text-center"
                              }, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_list_item_title, { class: "text-body-2 text-grey" }, {
                                    default: _withCtx(() => _cache[92] || (_cache[92] = [
                                      _createTextVNode("该目录为空或访问受限")
                                    ])),
                                    _: 1
                                  })
                                ]),
                                _: 1
                              }))
                            : _createCommentVNode("", true)
                        ]),
                        _: 1
                      })
                    ])),
                (dirDialog.error)
                  ? (_openBlock(), _createBlock(_component_v_alert, {
                      key: 2,
                      type: "error",
                      density: "compact",
                      class: "mt-2 text-caption",
                      variant: "tonal"
                    }, {
                      default: _withCtx(() => [
                        _createTextVNode(_toDisplayString(dirDialog.error), 1)
                      ]),
                      _: 1
                    }))
                  : _createCommentVNode("", true)
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_actions, { class: "px-3 py-1" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  color: "primary",
                  onClick: confirmDirSelection,
                  disabled: !dirDialog.currentPath || dirDialog.loading,
                  variant: "text",
                  size: "small"
                }, {
                  default: _withCtx(() => _cache[93] || (_cache[93] = [
                    _createTextVNode(" 选择当前目录 ")
                  ])),
                  _: 1
                }, 8, ["disabled"]),
                _createVNode(_component_v_btn, {
                  color: "grey",
                  onClick: closeDirDialog,
                  variant: "text",
                  size: "small"
                }, {
                  default: _withCtx(() => _cache[94] || (_cache[94] = [
                    _createTextVNode(" 取消 ")
                  ])),
                  _: 1
                })
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }, 8, ["modelValue"])
  ], 64))
}
}

};
const Page = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-383f4b75"]]);

export { Page as default };
