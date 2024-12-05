import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {resolveComponent:_resolveComponent,createVNode:_createVNode,createElementVNode:_createElementVNode,withCtx:_withCtx,toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,renderList:_renderList,Fragment:_Fragment,createElementBlock:_createElementBlock,withKeys:_withKeys} = await importShared('vue');


const _hoisted_1 = { class: "plugin-config" };
const _hoisted_2 = {
  key: 2,
  class: "my-1"
};
const _hoisted_3 = { class: "d-flex flex-column" };
const _hoisted_4 = { class: "path-selector flex-grow-1 mr-2" };
const _hoisted_5 = { class: "path-selector flex-grow-1 ml-2" };
const _hoisted_6 = { class: "d-flex flex-column" };
const _hoisted_7 = { class: "path-selector flex-grow-1 mr-2" };
const _hoisted_8 = { class: "path-selector flex-grow-1 ml-2" };
const _hoisted_9 = { class: "d-flex flex-column" };
const _hoisted_10 = { class: "path-selector flex-grow-1 mr-2" };
const _hoisted_11 = { class: "path-selector flex-grow-1 ml-2" };
const _hoisted_12 = { class: "d-flex flex-column" };
const _hoisted_13 = { class: "path-selector flex-grow-1 mr-2" };
const _hoisted_14 = { class: "path-selector flex-grow-1 ml-2" };
const _hoisted_15 = { class: "d-flex flex-column" };
const _hoisted_16 = { class: "path-selector flex-grow-1 mr-2" };
const _hoisted_17 = { class: "path-selector flex-grow-1 ml-2" };
const _hoisted_18 = { class: "d-flex flex-column" };
const _hoisted_19 = {
  key: 0,
  class: "d-flex justify-center my-3"
};
const _hoisted_20 = { key: 1 };
const _hoisted_21 = {
  key: 1,
  class: "d-flex flex-column align-center py-3"
};
const _hoisted_22 = {
  key: 2,
  class: "d-flex flex-column align-center"
};
const _hoisted_23 = { class: "d-flex flex-column align-center mb-3" };
const _hoisted_24 = ["src"];
const _hoisted_25 = { class: "text-body-2 text-grey mb-1" };
const _hoisted_26 = { class: "text-subtitle-2 font-weight-medium text-primary" };
const _hoisted_27 = {
  key: 3,
  class: "d-flex flex-column align-center py-3"
};
const _hoisted_28 = { class: "text-caption mt-2 text-grey" };

const {ref,reactive,computed,onMounted,watch} = await importShared('vue');


const PLUGIN_ID = "P115StrmHelper";

// 状态变量

const _sfc_main = {
  __name: 'Config',
  props: {
  api: {
    type: [Object, Function],
    required: true
  },
  initialConfig: {
    type: Object,
    default: () => ({})
  }
},
  emits: ['save', 'close', 'switch'],
  setup(__props, { emit: __emit }) {

const props = __props;

const emit = __emit;

// 定义插件ID常量，修复pluginId未定义错误
const loading = ref(true);
const saveLoading = ref(false);
const syncLoading = ref(false);
ref(false);
const activeTab = ref('tab-transfer');
const mediaservers = ref([]);
const isCookieVisible = ref(false); // <-- 新增状态变量
const config = reactive({
  enabled: false,
  cookies: '',
  password: '',
  moviepilot_address: '',
  user_rmt_mediaext: 'mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v',
  user_download_mediaext: 'srt,ssa,ass',
  transfer_monitor_enabled: false,
  transfer_monitor_scrape_metadata_enabled: false,
  transfer_monitor_paths: '',
  transfer_mp_mediaserver_paths: '',
  transfer_monitor_media_server_refresh_enabled: false,
  transfer_monitor_mediaservers: [],
  timing_full_sync_strm: false,
  full_sync_auto_download_mediainfo_enabled: false,
  cron_full_sync_strm: '0 */7 * * *',
  full_sync_strm_paths: '',
  monitor_life_enabled: false,
  monitor_life_auto_download_mediainfo_enabled: false,
  monitor_life_paths: '',
  monitor_life_mp_mediaserver_paths: '',
  monitor_life_media_server_refresh_enabled: false,
  monitor_life_mediaservers: [],
  monitor_life_auto_remove_local_enabled: false,
  monitor_life_scrape_metadata_enabled: false,
  share_strm_auto_download_mediainfo_enabled: false,
  user_share_code: '',
  user_receive_code: '',
  user_share_link: '',
  user_share_pan_path: '/',
  user_share_local_path: '',
  clear_recyclebin_enabled: false,
  clear_receive_path_enabled: false,
  cron_clear: '0 */7 * * *',
  pan_transfer_enabled: false,
  pan_transfer_paths: '',
});

// 消息提示
const message = reactive({
  text: '',
  type: 'info'
});

// 路径管理
const transferPaths = ref([{ local: '', remote: '' }]);
const transferMpPaths = ref([{ local: '', remote: '' }]);
const fullSyncPaths = ref([{ local: '', remote: '' }]);
const monitorLifePaths = ref([{ local: '', remote: '' }]);
const monitorLifeMpPaths = ref([{ local: '', remote: '' }]);
const panTransferPaths = ref([{ path: '' }]);

// 目录选择器对话框
const dirDialog = reactive({
  show: false,
  isLocal: true,
  loading: false,
  error: null,
  currentPath: '/',
  items: [],
  selectedPath: '',
  callback: null,
  type: '',
  index: -1
});

// 二维码登录对话框
const qrDialog = reactive({
  show: false,
  loading: false,
  error: null,
  qrcode: '',
  uid: '',
  time: "",
  sgin: "",
  tips: '请使用支付宝扫描二维码登录',
  status: '等待扫码',
  checkInterval: null,
  clientType: 'alipaymini'
});

// 二维码客户端类型选项
const clientTypes = [
  { label: "支付宝", value: "alipaymini" },
  { label: "微信", value: "wechatmini" },
  { label: "安卓", value: "115android" },
  { label: "iOS", value: "115ios" },
  { label: "网页", value: "web" },
  { label: "PAD", value: "115ipad" },
  { label: "TV", value: "tv" }
];

// 监视config中的路径配置，同步到可视化组件
watch(() => config.transfer_monitor_paths, (newVal) => {
  if (!newVal) {
    transferPaths.value = [{ local: '', remote: '' }];
    return;
  }

  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    transferPaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });

    if (transferPaths.value.length === 0) {
      transferPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析transfer_monitor_paths出错:', e);
    transferPaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.transfer_mp_mediaserver_paths, (newVal) => {
  if (!newVal) {
    transferMpPaths.value = [{ local: '', remote: '' }];
    return;
  }

  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    transferMpPaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });

    if (transferMpPaths.value.length === 0) {
      transferMpPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析transfer_mp_mediaserver_paths出错:', e);
    transferMpPaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.full_sync_strm_paths, (newVal) => {
  if (!newVal) {
    fullSyncPaths.value = [{ local: '', remote: '' }];
    return;
  }

  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    fullSyncPaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });

    if (fullSyncPaths.value.length === 0) {
      fullSyncPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析full_sync_strm_paths出错:', e);
    fullSyncPaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.monitor_life_paths, (newVal) => {
  if (!newVal) {
    monitorLifePaths.value = [{ local: '', remote: '' }];
    return;
  }

  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    monitorLifePaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });

    if (monitorLifePaths.value.length === 0) {
      monitorLifePaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析monitor_life_paths出错:', e);
    monitorLifePaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.monitor_life_mp_mediaserver_paths, (newVal) => {
  if (!newVal) {
    monitorLifeMpPaths.value = [{ local: '', remote: '' }];
    return;
  }

  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    monitorLifeMpPaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });

    if (monitorLifeMpPaths.value.length === 0) {
      monitorLifeMpPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析monitor_life_mp_mediaserver_paths出错:', e);
    monitorLifeMpPaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.pan_transfer_paths, (newVal) => {
  if (!newVal) {
    panTransferPaths.value = [{ path: '' }];
    return;
  }

  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    panTransferPaths.value = paths.map(path => {
      return { path };
    });

    if (panTransferPaths.value.length === 0) {
      panTransferPaths.value = [{ path: '' }];
    }
  } catch (e) {
    console.error('解析pan_transfer_paths出错:', e);
    panTransferPaths.value = [{ path: '' }];
  }
}, { immediate: true });

// 从路径对象列表生成配置字符串
const generatePathsConfig = (paths, key) => {
  const configText = paths.map(p => {
    if (key === 'panTransfer') {
      return p.path?.trim();
    } else {
      return `${p.local?.trim()}#${p.remote?.trim()}`;
    }
  }).filter(p => {
    if (key === 'panTransfer') {
      return p && p !== '';
    } else {
      return p !== '#' && p !== '';
    }
  }).join('\n');

  return configText;
};

// 加载配置
const loadConfig = async () => {
  try {
    loading.value = true;
    const data = await props.api.get(`plugin/${PLUGIN_ID}/get_config`);
    if (data) {
      // 更新配置
      Object.assign(config, data);

      // 保存媒体服务器列表
      if (data.mediaservers) {
        mediaservers.value = data.mediaservers;
      }

      const p115LocalPaths = new Set();
      if (config.transfer_monitor_paths) {
        config.transfer_monitor_paths.split('\\n')
          .map(p => p.split('#')[0]?.trim()).filter(p => p).forEach(p => p115LocalPaths.add(p));
      }
      if (config.full_sync_strm_paths) {
        config.full_sync_strm_paths.split('\\n')
          .map(p => p.split('#')[0]?.trim()).filter(p => p).forEach(p => p115LocalPaths.add(p));
      }
      if (config.monitor_life_paths) {
        config.monitor_life_paths.split('\\n')
          .map(p => p.split('#')[0]?.trim()).filter(p => p).forEach(p => p115LocalPaths.add(p));
      }

    }
  } catch (err) {
    console.error('加载配置失败:', err);
    message.text = `加载配置失败: ${err.message || '未知错误'}`;
    message.type = 'error';
  } finally {
    loading.value = false;
  }
};

// 保存配置
const saveConfig = async () => {
  saveLoading.value = true;
  message.text = '';
  message.type = 'info';

  try {
    // 1. 更新配置对象中的路径字符串 (这部分逻辑保持不变)
    config.transfer_monitor_paths = generatePathsConfig(transferPaths.value, 'transfer');
    config.transfer_mp_mediaserver_paths = generatePathsConfig(transferMpPaths.value, 'mp');
    config.full_sync_strm_paths = generatePathsConfig(fullSyncPaths.value, 'fullSync');
    config.monitor_life_paths = generatePathsConfig(monitorLifePaths.value, 'monitorLife');
    config.monitor_life_mp_mediaserver_paths = generatePathsConfig(monitorLifeMpPaths.value, 'monitorLifeMp');
    config.pan_transfer_paths = generatePathsConfig(panTransferPaths.value, 'panTransfer');

    // 2. 【重要】通过 emit 事件将配置数据发送给 MoviePilot 框架
    //    使用 JSON.parse(JSON.stringify(...)) 确保传递的是纯对象
    emit('save', JSON.parse(JSON.stringify(config)));

    // 3. (可选) 显示本地的临时反馈信息
    message.text = '配置已发送保存请求，请稍候...';
    message.type = 'info';

    // 注意：不再需要检查后端API的响应，因为保存由框架处理
    // 注意：也不再需要返回 true/false，因为操作是异步触发的

  } catch (err) {
    // 这个 catch 块现在主要处理 emit 或 generatePathsConfig 可能出现的错误
    console.error('发送保存事件时出错:', err);
    message.text = `发送保存请求时出错: ${err.message || '未知错误'}`;
    message.type = 'error';
  } finally {
    saveLoading.value = false;
    // 延迟清除临时消息
    setTimeout(() => {
      // 只清除临时的 'info' 或 'error' 消息
      if (message.type === 'info' || message.type === 'error') {
        message.text = '';
      }
    }, 5000);
  }
};

// 触发全量同步
const triggerFullSync = async () => {
  syncLoading.value = true;
  message.text = '';

  try {
    // 检查插件是否已启用
    if (!config.enabled) {
      throw new Error('插件未启用，请先启用插件');
    }

    // 检查是否已配置Cookie
    if (!config.cookies || config.cookies.trim() === '') {
      throw new Error('请先设置115 Cookie');
    }

    // 同步路径设置到配置对象
    config.full_sync_strm_paths = generatePathsConfig(fullSyncPaths.value, 'fullSync');

    // 检查是否有有效路径配置
    if (!config.full_sync_strm_paths) {
      throw new Error('请先配置全量同步路径');
    }

    // 使用常量PLUGIN_ID

    // 调用API触发全量同步
    const result = await props.api.post(`plugin/${PLUGIN_ID}/full_sync`);

    if (result && result.code === 0) {
      message.text = result.msg || '全量同步任务已启动';
      message.type = 'success';
    } else {
      throw new Error(result?.msg || '启动全量同步失败');
    }
  } catch (err) {
    message.text = `启动全量同步失败: ${err.message || '未知错误'}`;
    message.type = 'error';
    console.error('启动全量同步失败:', err);
  } finally {
    syncLoading.value = false;
  }
};

// 路径管理方法
const addPath = (type) => {
  switch (type) {
    case 'transfer':
      transferPaths.value.push({ local: '', remote: '' });
      break;
    case 'mp':
      transferMpPaths.value.push({ local: '', remote: '' });
      break;
    case 'fullSync':
      fullSyncPaths.value.push({ local: '', remote: '' });
      break;
    case 'monitorLife':
      monitorLifePaths.value.push({ local: '', remote: '' });
      break;
    case 'monitorLifeMp':
      monitorLifeMpPaths.value.push({ local: '', remote: '' });
      break;
  }
};

const removePath = (index, type) => {
  switch (type) {
    case 'transfer':
      transferPaths.value.splice(index, 1);
      if (transferPaths.value.length === 0) {
        transferPaths.value = [{ local: '', remote: '' }];
      }
      break;
    case 'mp':
      transferMpPaths.value.splice(index, 1);
      if (transferMpPaths.value.length === 0) {
        transferMpPaths.value = [{ local: '', remote: '' }];
      }
      break;
    case 'fullSync':
      fullSyncPaths.value.splice(index, 1);
      if (fullSyncPaths.value.length === 0) {
        fullSyncPaths.value = [{ local: '', remote: '' }];
      }
      break;
    case 'monitorLife':
      monitorLifePaths.value.splice(index, 1);
      if (monitorLifePaths.value.length === 0) {
        monitorLifePaths.value = [{ local: '', remote: '' }];
      }
      break;
    case 'monitorLifeMp':
      monitorLifeMpPaths.value.splice(index, 1);
      if (monitorLifeMpPaths.value.length === 0) {
        monitorLifeMpPaths.value = [{ local: '', remote: '' }];
      }
      break;
  }
};

const addPanTransferPath = () => {
  panTransferPaths.value.push({ path: '' });
};

const removePanTransferPath = (index) => {
  panTransferPaths.value.splice(index, 1);
  if (panTransferPaths.value.length === 0) {
    panTransferPaths.value = [{ path: '' }];
  }
};

// 目录选择器方法
const openDirSelector = (index, locationType, pathType) => {
  dirDialog.show = true;
  dirDialog.isLocal = locationType === 'local';
  dirDialog.loading = false;
  dirDialog.error = null;
  dirDialog.items = [];
  dirDialog.index = index;
  dirDialog.type = pathType;

  // 设置初始路径
  if (dirDialog.isLocal) {
    dirDialog.currentPath = '/';
  } else {
    dirDialog.currentPath = '/';
  }

  // 加载目录内容
  loadDirContent();
};

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
      // 使用常量PLUGIN_ID

      // 检查cookie是否已设置
      if (!config.cookies || config.cookies.trim() === '') {
        throw new Error('请先设置115 Cookie才能浏览网盘目录');
      }

      // 调用API获取目录内容
      const result = await props.api.get(`plugin/${PLUGIN_ID}/browse_dir?path=${encodeURIComponent(dirDialog.currentPath)}&is_local=${dirDialog.isLocal}`);

      if (result && result.code === 0 && result.items) {
        dirDialog.items = result.items
          .filter(item => item.is_dir) // 只保留目录
          .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
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

const selectDir = (item) => {
  if (!item || !item.is_dir) return;

  if (item.path) {
    dirDialog.currentPath = item.path;
    loadDirContent();
  }
};

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

const confirmDirSelection = () => {
  if (!dirDialog.currentPath) return;

  let processedPath = dirDialog.currentPath;
  // 移除末尾的斜杠，除非路径是 "/" 或者类似 "C:/" 的驱动器根目录
  if (processedPath !== '/' &&
    !(/^[a-zA-Z]:[\\\/]$/.test(processedPath)) &&
    (processedPath.endsWith('/') || processedPath.endsWith('\\\\'))) {
    processedPath = processedPath.slice(0, -1);
  }

  if (dirDialog.index >= 0) {
    // 根据类型设置路径
    switch (dirDialog.type) {
      case 'transfer':
        if (dirDialog.isLocal) {
          transferPaths.value[dirDialog.index].local = processedPath;
        } else {
          transferPaths.value[dirDialog.index].remote = processedPath;
        }
        break;
      case 'fullSync':
        if (dirDialog.isLocal) {
          fullSyncPaths.value[dirDialog.index].local = processedPath;
        } else {
          fullSyncPaths.value[dirDialog.index].remote = processedPath;
        }
        break;
      case 'monitorLife':
        if (dirDialog.isLocal) {
          monitorLifePaths.value[dirDialog.index].local = processedPath;
        } else {
          monitorLifePaths.value[dirDialog.index].remote = processedPath;
        }
        break;
      case 'panTransfer':
        panTransferPaths.value[dirDialog.index].path = processedPath;
        break;
      case 'mediawarpStrm':
        mediawarpStrmLocalPaths.value[dirDialog.index] = processedPath;
        break;
    }
  } else if (dirDialog.type === 'sharePath') {
    // 这个分支主要用于 Page.vue 的分享路径，Config.vue 目前不直接使用此 type 进行赋值
    // 但保留逻辑以防未来扩展或共享组件
    if (dirDialog.isLocal) {
      config.user_share_local_path = processedPath;
    } else {
      config.user_share_pan_path = processedPath;
    }
  }

  // 关闭对话框
  closeDirDialog();
};

const closeDirDialog = () => {
  dirDialog.show = false;
  dirDialog.items = [];
  dirDialog.error = null;
};

// 新增：复制Cookie到剪贴板
const copyCookieToClipboard = async () => {
  if (!config.cookies) {
    message.text = 'Cookie为空，无法复制。';
    message.type = 'warning';
    return;
  }
  try {
    await navigator.clipboard.writeText(config.cookies);
    message.text = 'Cookie已复制到剪贴板！';
    message.type = 'success';
  } catch (err) {
    console.error('复制Cookie失败:', err);
    message.text = '复制Cookie失败。请检查浏览器权限或确保通过HTTPS访问，或尝试手动复制。';
    message.type = 'error';
  }
  setTimeout(() => {
    if (message.type === 'success' || message.type === 'warning' || message.type === 'error') {
      message.text = '';
    }
  }, 3000);
};

// 二维码登录方法
const openQrCodeDialog = () => {
  qrDialog.show = true;
  qrDialog.loading = false;
  qrDialog.error = null;
  qrDialog.qrcode = '';
  qrDialog.uid = '';
  qrDialog.time = '';
  qrDialog.sign = '';

  // 确保默认的 clientType 仍然是 clientTypes 中的一个有效值
  if (!clientTypes.some(ct => ct.value === qrDialog.clientType)) {
    qrDialog.clientType = 'alipaymini'; // 如果当前值无效，则重置为默认的支付宝
  }

  // 根据当前的 qrDialog.clientType 设置 tips
  const selectedClient = clientTypes.find(type => type.value === qrDialog.clientType);

  if (selectedClient) {
    qrDialog.tips = `请使用${selectedClient.label}扫描二维码登录`;
  } else {
    // 理应不会到这里，因为上面已经做了校验和重置
    qrDialog.clientType = 'alipaymini';
    qrDialog.tips = '请使用支付宝扫描二维码登录';
  }

  qrDialog.status = '等待扫码';
  getQrCode();
};

const getQrCode = async () => {
  qrDialog.loading = true;
  qrDialog.error = null;
  qrDialog.qrcode = '';
  qrDialog.uid = '';
  qrDialog.time = '';
  qrDialog.sign = '';

  // 在发送请求前打印实际使用的 clientType
  console.warn(`【115STRM助手 DEBUG】准备获取二维码，前端选择的 clientType: ${qrDialog.clientType}`);

  try {
    // 使用常量PLUGIN_ID
    const response = await props.api.get(`plugin/${PLUGIN_ID}/get_qrcode?client_type=${qrDialog.clientType}`);

    if (response && response.code === 0) {
      qrDialog.uid = response.uid;
      qrDialog.time = response.time;
      qrDialog.sign = response.sign;
      qrDialog.qrcode = response.qrcode;
      qrDialog.tips = response.tips || '请扫描二维码登录';
      qrDialog.status = '等待扫码';

      // 确保使用响应中的客户端类型，以防服务器调整
      if (response.client_type) {
        // console.warn(`【115STRM助手 DEBUG】后端返回 client_type: ${response.client_type}, 更新前端`);
        qrDialog.clientType = response.client_type;
      }

      startQrCodeCheckInterval();
    } else {
      qrDialog.error = response?.error || '获取二维码失败';
      // 增加日志，查看具体错误信息
      console.error("【115STRM助手 DEBUG】获取二维码API调用失败或返回错误码: ", response);
    }
  } catch (err) {
    qrDialog.error = `获取二维码出错: ${err.message || '未知错误'}`;
    console.error('【115STRM助手 DEBUG】获取二维码 JS 捕获异常:', err);
  } finally {
    qrDialog.loading = false;
  }
};

// 检查二维码状态
const checkQrCodeStatus = async () => {
  if (!qrDialog.uid || !qrDialog.show) return;
  if (!qrDialog.time || !qrDialog.show) return;
  if (!qrDialog.sign || !qrDialog.show) return;

  try {
    // 使用常量PLUGIN_ID
    const response = await props.api.get(`plugin/${PLUGIN_ID}/check_qrcode?uid=${qrDialog.uid}&time=${qrDialog.time}&sign=${qrDialog.sign}&client_type=${qrDialog.clientType}`);

    if (response && response.code === 0) {
      // 更新状态文本
      if (response.status === 'waiting') {
        qrDialog.status = '等待扫码';
      } else if (response.status === 'scanned') {
        qrDialog.status = '已扫码，请在设备上确认';
      } else if (response.status === 'success') {

        // 如果成功获取了cookie
        if (response.cookie) {
          // 停止轮询
          clearQrCodeCheckInterval();

          qrDialog.status = '登录成功！';

          // 更新配置中的 Cookie (前端显示会立即更新)
          config.cookies = response.cookie;

          // 【重要】不再自动触发保存事件 (emit)

          // 显示成功消息，提示用户需要手动保存
          message.text = '登录成功！Cookie已获取，请点击下方"保存配置"按钮保存。';
          message.type = 'success';

          // 延迟关闭 *仅* QR 对话框
          setTimeout(() => {
            qrDialog.show = false;
          }, 3000); // 3秒延迟，让用户看到消息

          // 移除之前的 emit 调用和相关处理

        } else {
          // status 为 success 但没有 cookie
          qrDialog.status = '登录似乎成功，但未获取到Cookie';
          message.text = '登录成功但未获取到Cookie信息，请重试或检查账号。';
          message.type = 'warning';
          // 停止轮询
          clearQrCodeCheckInterval();
        }
      }
    } else if (response && response.code === -1) {
      // 二维码出错或过期 (仅在非成功获取Cookie的情况下处理)
      if (qrDialog.status !== '登录成功，正在处理...') { // 避免覆盖成功后的状态
        clearQrCodeCheckInterval();
        qrDialog.error = response.error || '二维码已失效，请刷新';
        qrDialog.status = '二维码已失效';
      }
    }
  } catch (err) {
    // 仅在非成功获取Cookie的情况下处理网络等错误
    if (qrDialog.status !== '登录成功，正在处理...') {
      console.error('检查二维码状态JS捕获异常:', err);
      // 可以在这里添加一些错误提示，但要避免频繁报错干扰用户
      // qrDialog.error = `检查状态出错: ${err.message}`;
    }
  }
};

// 开始二维码状态检查定时器
const startQrCodeCheckInterval = () => {
  // 先清除可能存在的定时器
  clearQrCodeCheckInterval();

  // 设置新的定时器，每3秒检查一次
  qrDialog.checkIntervalId = setInterval(checkQrCodeStatus, 3000);
};

const clearQrCodeCheckInterval = () => {
  if (qrDialog.checkIntervalId) {
    clearInterval(qrDialog.checkIntervalId);
    qrDialog.checkIntervalId = null;
  }
};

const refreshQrCode = () => {
  // 清除之前的状态和定时器
  clearQrCodeCheckInterval();
  qrDialog.error = null;

  // 根据当前选择的客户端类型调整提示
  switch (qrDialog.clientType) {
    case 'alipaymini':
      qrDialog.tips = '请使用支付宝扫描二维码登录';
      break;
    case 'wechatmini':
      qrDialog.tips = '请使用微信扫描二维码登录';
      break;
    case '115android':
      qrDialog.tips = '请使用115安卓客户端扫描登录';
      break;
    case '115ios':
      qrDialog.tips = '请使用115 iOS客户端扫描登录';
      break;
    case 'web':
      qrDialog.tips = '请使用115网页版扫码登录';
      break;
    default:
      // 如果匹配不到，尝试从 clientTypes 数组中查找 label
      const matchedType = clientTypes.find(type => type.value === qrDialog.clientType);
      if (matchedType) {
        qrDialog.tips = `请使用${matchedType.label}扫描二维码登录`;
      } else {
        qrDialog.tips = '请扫描二维码登录'; // 最终回退
      }
  }

  // 重新获取二维码
  getQrCode();
};

const closeQrDialog = () => {
  clearQrCodeCheckInterval();
  qrDialog.show = false;
};

// 组件生命周期
onMounted(() => {
  loadConfig();
});

// 监听 qrDialog.clientType 的变化来调用 refreshQrCode
watch(() => qrDialog.clientType, (newVal, oldVal) => {
  // 仅当值实际改变且对话框可见时才刷新
  if (newVal !== oldVal && qrDialog.show) {
    console.log(`【115STRM助手 DEBUG】qrDialog.clientType 从 ${oldVal} 变为 ${newVal}，准备刷新二维码`);
    refreshQrCode();
  }
});

// 新增：设置MoviePilot地址为当前源
const setMoviePilotAddressToCurrentOrigin = () => {
  if (window && window.location && window.location.origin) {
    config.moviepilot_address = window.location.origin;
    message.text = 'MoviePilot地址已设置为当前站点地址！';
    message.type = 'success';
  } else {
    message.text = '无法获取当前站点地址。';
    message.type = 'error';
  }
  setTimeout(() => {
    if (message.type === 'success' || message.type === 'error') {
      message.text = '';
    }
  }, 3000);
};

return (_ctx, _cache) => {
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_card_title = _resolveComponent("v-card-title");
  const _component_v_alert = _resolveComponent("v-alert");
  const _component_v_skeleton_loader = _resolveComponent("v-skeleton-loader");
  const _component_v_switch = _resolveComponent("v-switch");
  const _component_v_col = _resolveComponent("v-col");
  const _component_v_text_field = _resolveComponent("v-text-field");
  const _component_v_row = _resolveComponent("v-row");
  const _component_v_card_text = _resolveComponent("v-card-text");
  const _component_v_card = _resolveComponent("v-card");
  const _component_v_tab = _resolveComponent("v-tab");
  const _component_v_tabs = _resolveComponent("v-tabs");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_select = _resolveComponent("v-select");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_window_item = _resolveComponent("v-window-item");
  const _component_VCronField = _resolveComponent("VCronField");
  const _component_v_window = _resolveComponent("v-window");
  const _component_v_spacer = _resolveComponent("v-spacer");
  const _component_v_card_actions = _resolveComponent("v-card-actions");
  const _component_v_progress_circular = _resolveComponent("v-progress-circular");
  const _component_v_list_item_title = _resolveComponent("v-list-item-title");
  const _component_v_list_item_subtitle = _resolveComponent("v-list-item-subtitle");
  const _component_v_list_item = _resolveComponent("v-list-item");
  const _component_v_list = _resolveComponent("v-list");
  const _component_v_dialog = _resolveComponent("v-dialog");
  const _component_v_chip = _resolveComponent("v-chip");
  const _component_v_chip_group = _resolveComponent("v-chip-group");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createVNode(_component_v_card, {
      flat: "",
      class: "rounded border",
      style: {"display":"flex","flex-direction":"column","max-height":"85vh"}
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card_title, { class: "text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5" }, {
          default: _withCtx(() => [
            _createVNode(_component_v_icon, {
              icon: "mdi-cog",
              class: "mr-2",
              color: "primary",
              size: "small"
            }),
            _cache[36] || (_cache[36] = _createElementVNode("span", null, "115网盘STRM助手配置", -1))
          ]),
          _: 1
        }),
        _createVNode(_component_v_card_text, {
          class: "px-3 py-2",
          style: {"flex-grow":"1","overflow-y":"auto","padding-bottom":"56px"}
        }, {
          default: _withCtx(() => [
            (message.text)
              ? (_openBlock(), _createBlock(_component_v_alert, {
                  key: 0,
                  type: message.type,
                  density: "compact",
                  class: "mb-2 text-caption",
                  variant: "tonal",
                  closable: ""
                }, {
                  default: _withCtx(() => [
                    _createTextVNode(_toDisplayString(message.text), 1)
                  ]),
                  _: 1
                }, 8, ["type"]))
              : _createCommentVNode("", true),
            (loading.value)
              ? (_openBlock(), _createBlock(_component_v_skeleton_loader, {
                  key: 1,
                  type: "article, actions"
                }))
              : (_openBlock(), _createElementBlock("div", _hoisted_2, [
                  _createVNode(_component_v_card, {
                    flat: "",
                    class: "rounded mb-3 border config-card"
                  }, {
                    default: _withCtx(() => [
                      _createVNode(_component_v_card_title, { class: "text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-lighten-5" }, {
                        default: _withCtx(() => [
                          _createVNode(_component_v_icon, {
                            icon: "mdi-cog",
                            class: "mr-2",
                            color: "primary",
                            size: "small"
                          }),
                          _cache[37] || (_cache[37] = _createElementVNode("span", null, "基础设置", -1))
                        ]),
                        _: 1
                      }),
                      _createVNode(_component_v_card_text, { class: "pa-3" }, {
                        default: _withCtx(() => [
                          _createVNode(_component_v_row, null, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_col, {
                                cols: "12",
                                md: "2"
                              }, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_switch, {
                                    modelValue: config.enabled,
                                    "onUpdate:modelValue": _cache[0] || (_cache[0] = $event => ((config.enabled) = $event)),
                                    label: "启用插件",
                                    color: "success",
                                    density: "compact"
                                  }, null, 8, ["modelValue"])
                                ]),
                                _: 1
                              }),
                              _createVNode(_component_v_col, {
                                cols: "12",
                                md: "5"
                              }, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_text_field, {
                                    modelValue: config.cookies,
                                    "onUpdate:modelValue": _cache[2] || (_cache[2] = $event => ((config.cookies) = $event)),
                                    label: "115 Cookie",
                                    hint: "点击图标切换显隐、复制或扫码",
                                    "persistent-hint": "",
                                    density: "compact",
                                    variant: "outlined",
                                    "hide-details": "auto",
                                    type: isCookieVisible.value ? 'text' : 'password'
                                  }, {
                                    "append-inner": _withCtx(() => [
                                      _createVNode(_component_v_icon, {
                                        icon: isCookieVisible.value ? 'mdi-eye-off' : 'mdi-eye',
                                        onClick: _cache[1] || (_cache[1] = $event => (isCookieVisible.value = !isCookieVisible.value)),
                                        "aria-label": isCookieVisible.value ? '隐藏Cookie' : '显示Cookie',
                                        title: isCookieVisible.value ? '隐藏Cookie' : '显示Cookie',
                                        class: "mr-1",
                                        size: "small"
                                      }, null, 8, ["icon", "aria-label", "title"]),
                                      _createVNode(_component_v_icon, {
                                        icon: "mdi-content-copy",
                                        onClick: copyCookieToClipboard,
                                        disabled: !config.cookies,
                                        "aria-label": "复制Cookie",
                                        title: "复制Cookie到剪贴板",
                                        size: "small",
                                        class: "mr-1"
                                      }, null, 8, ["disabled"])
                                    ]),
                                    append: _withCtx(() => [
                                      _createVNode(_component_v_icon, {
                                        icon: "mdi-qrcode-scan",
                                        onClick: openQrCodeDialog,
                                        color: config.cookies ? 'success' : 'default',
                                        "aria-label": config.cookies ? '更新/更换Cookie (重新扫码)' : '扫码获取Cookie',
                                        title: config.cookies ? '更新/更换Cookie (重新扫码)' : '扫码获取Cookie'
                                      }, null, 8, ["color", "aria-label", "title"])
                                    ]),
                                    _: 1
                                  }, 8, ["modelValue", "type"])
                                ]),
                                _: 1
                              }),
                              _createVNode(_component_v_col, {
                                cols: "12",
                                md: "5"
                              }, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_text_field, {
                                    modelValue: config.moviepilot_address,
                                    "onUpdate:modelValue": _cache[3] || (_cache[3] = $event => ((config.moviepilot_address) = $event)),
                                    label: "MoviePilot 内网访问地址",
                                    hint: "点右侧图标自动填充当前站点地址。",
                                    "persistent-hint": "",
                                    density: "compact",
                                    variant: "outlined",
                                    "hide-details": "auto"
                                  }, {
                                    append: _withCtx(() => [
                                      _createVNode(_component_v_icon, {
                                        icon: "mdi-web",
                                        onClick: setMoviePilotAddressToCurrentOrigin,
                                        "aria-label": "使用当前站点地址",
                                        title: "使用当前站点地址",
                                        color: "info"
                                      })
                                    ]),
                                    _: 1
                                  }, 8, ["modelValue"])
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
                                    modelValue: config.user_rmt_mediaext,
                                    "onUpdate:modelValue": _cache[4] || (_cache[4] = $event => ((config.user_rmt_mediaext) = $event)),
                                    label: "可整理媒体文件扩展名",
                                    hint: "支持的媒体文件扩展名，多个用逗号分隔",
                                    "persistent-hint": "",
                                    density: "compact",
                                    variant: "outlined",
                                    "hide-details": "auto"
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
                                    modelValue: config.user_download_mediaext,
                                    "onUpdate:modelValue": _cache[5] || (_cache[5] = $event => ((config.user_download_mediaext) = $event)),
                                    label: "可下载媒体数据文件扩展名",
                                    hint: "下载的字幕等附属文件扩展名，多个用逗号分隔",
                                    "persistent-hint": "",
                                    density: "compact",
                                    variant: "outlined",
                                    "hide-details": "auto"
                                  }, null, 8, ["modelValue"])
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
                      _createVNode(_component_v_tabs, {
                        modelValue: activeTab.value,
                        "onUpdate:modelValue": _cache[6] || (_cache[6] = $event => ((activeTab).value = $event)),
                        color: "primary",
                        "bg-color": "grey-lighten-3",
                        class: "rounded-t",
                        grow: ""
                      }, {
                        default: _withCtx(() => [
                          _createVNode(_component_v_tab, {
                            value: "tab-transfer",
                            class: "text-caption"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                start: ""
                              }, {
                                default: _withCtx(() => _cache[38] || (_cache[38] = [
                                  _createTextVNode("mdi-file-move-outline")
                                ])),
                                _: 1
                              }),
                              _cache[39] || (_cache[39] = _createTextVNode("监控MP整理 "))
                            ]),
                            _: 1
                          }),
                          _createVNode(_component_v_tab, {
                            value: "tab-sync",
                            class: "text-caption"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                start: ""
                              }, {
                                default: _withCtx(() => _cache[40] || (_cache[40] = [
                                  _createTextVNode("mdi-sync")
                                ])),
                                _: 1
                              }),
                              _cache[41] || (_cache[41] = _createTextVNode("全量同步 "))
                            ]),
                            _: 1
                          }),
                          _createVNode(_component_v_tab, {
                            value: "tab-life",
                            class: "text-caption"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                start: ""
                              }, {
                                default: _withCtx(() => _cache[42] || (_cache[42] = [
                                  _createTextVNode("mdi-calendar-heart")
                                ])),
                                _: 1
                              }),
                              _cache[43] || (_cache[43] = _createTextVNode("监控115生活事件 "))
                            ]),
                            _: 1
                          }),
                          _createVNode(_component_v_tab, {
                            value: "tab-cleanup",
                            class: "text-caption"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                start: ""
                              }, {
                                default: _withCtx(() => _cache[44] || (_cache[44] = [
                                  _createTextVNode("mdi-broom")
                                ])),
                                _: 1
                              }),
                              _cache[45] || (_cache[45] = _createTextVNode("定期清理 "))
                            ]),
                            _: 1
                          }),
                          _createVNode(_component_v_tab, {
                            value: "tab-pan-transfer",
                            class: "text-caption"
                          }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                start: ""
                              }, {
                                default: _withCtx(() => _cache[46] || (_cache[46] = [
                                  _createTextVNode("mdi-transfer")
                                ])),
                                _: 1
                              }),
                              _cache[47] || (_cache[47] = _createTextVNode("网盘整理 "))
                            ]),
                            _: 1
                          })
                        ]),
                        _: 1
                      }, 8, ["modelValue"]),
                      _createVNode(_component_v_divider),
                      _createVNode(_component_v_window, {
                        modelValue: activeTab.value,
                        "onUpdate:modelValue": _cache[30] || (_cache[30] = $event => ((activeTab).value = $event))
                      }, {
                        default: _withCtx(() => [
                          _createVNode(_component_v_window_item, { value: "tab-transfer" }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_card_text, null, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_row, null, {
                                    default: _withCtx(() => [
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.transfer_monitor_enabled,
                                            "onUpdate:modelValue": _cache[7] || (_cache[7] = $event => ((config.transfer_monitor_enabled) = $event)),
                                            label: "整理事件监控",
                                            color: "info"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.transfer_monitor_scrape_metadata_enabled,
                                            "onUpdate:modelValue": _cache[8] || (_cache[8] = $event => ((config.transfer_monitor_scrape_metadata_enabled) = $event)),
                                            label: "STRM自动刮削",
                                            color: "primary"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.transfer_monitor_media_server_refresh_enabled,
                                            "onUpdate:modelValue": _cache[9] || (_cache[9] = $event => ((config.transfer_monitor_media_server_refresh_enabled) = $event)),
                                            label: "媒体服务器刷新",
                                            color: "warning"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_select, {
                                            modelValue: config.transfer_monitor_mediaservers,
                                            "onUpdate:modelValue": _cache[10] || (_cache[10] = $event => ((config.transfer_monitor_mediaservers) = $event)),
                                            label: "媒体服务器",
                                            items: mediaservers.value,
                                            multiple: "",
                                            chips: "",
                                            "closable-chips": ""
                                          }, null, 8, ["modelValue", "items"])
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
                                          _createElementVNode("div", _hoisted_3, [
                                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(transferPaths.value, (pair, index) => {
                                              return (_openBlock(), _createElementBlock("div", {
                                                key: `transfer-${index}`,
                                                class: "mb-2 d-flex align-center"
                                              }, [
                                                _createElementVNode("div", _hoisted_4, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.local,
                                                    "onUpdate:modelValue": $event => ((pair.local) = $event),
                                                    label: "本地STRM目录",
                                                    density: "compact",
                                                    "append-icon": "mdi-folder",
                                                    "onClick:append": $event => (openDirSelector(index, 'local', 'transfer'))
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue", "onClick:append"])
                                                ]),
                                                _createVNode(_component_v_icon, null, {
                                                  default: _withCtx(() => _cache[48] || (_cache[48] = [
                                                    _createTextVNode("mdi-pound")
                                                  ])),
                                                  _: 1
                                                }),
                                                _createElementVNode("div", _hoisted_5, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.remote,
                                                    "onUpdate:modelValue": $event => ((pair.remote) = $event),
                                                    label: "网盘媒体库目录",
                                                    density: "compact",
                                                    "append-icon": "mdi-folder-network",
                                                    "onClick:append": $event => (openDirSelector(index, 'remote', 'transfer'))
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue", "onClick:append"])
                                                ]),
                                                _createVNode(_component_v_btn, {
                                                  icon: "",
                                                  size: "small",
                                                  color: "error",
                                                  class: "ml-2",
                                                  onClick: $event => (removePath(index, 'transfer'))
                                                }, {
                                                  default: _withCtx(() => [
                                                    _createVNode(_component_v_icon, null, {
                                                      default: _withCtx(() => _cache[49] || (_cache[49] = [
                                                        _createTextVNode("mdi-delete")
                                                      ])),
                                                      _: 1
                                                    })
                                                  ]),
                                                  _: 2
                                                }, 1032, ["onClick"])
                                              ]))
                                            }), 128)),
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              "prepend-icon": "mdi-plus",
                                              variant: "outlined",
                                              class: "mt-2 align-self-start",
                                              onClick: _cache[11] || (_cache[11] = $event => (addPath('transfer')))
                                            }, {
                                              default: _withCtx(() => _cache[50] || (_cache[50] = [
                                                _createTextVNode(" 添加路径 ")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _createVNode(_component_v_alert, {
                                            type: "info",
                                            variant: "tonal",
                                            density: "compact",
                                            class: "mt-2"
                                          }, {
                                            default: _withCtx(() => _cache[51] || (_cache[51] = [
                                              _createTextVNode(" 监控MoviePilot整理入库事件，自动在此处配置的本地目录生成对应的STRM文件。"),
                                              _createElementVNode("br", null, null, -1),
                                              _createTextVNode(" 格式：本地STRM目录#网盘媒体库目录 ")
                                            ])),
                                            _: 1
                                          })
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
                                          _createElementVNode("div", _hoisted_6, [
                                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(transferMpPaths.value, (pair, index) => {
                                              return (_openBlock(), _createElementBlock("div", {
                                                key: `mp-${index}`,
                                                class: "mb-2 d-flex align-center"
                                              }, [
                                                _createElementVNode("div", _hoisted_7, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.local,
                                                    "onUpdate:modelValue": $event => ((pair.local) = $event),
                                                    label: "媒体库服务器映射目录",
                                                    density: "compact"
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue"])
                                                ]),
                                                _createVNode(_component_v_icon, null, {
                                                  default: _withCtx(() => _cache[52] || (_cache[52] = [
                                                    _createTextVNode("mdi-pound")
                                                  ])),
                                                  _: 1
                                                }),
                                                _createElementVNode("div", _hoisted_8, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.remote,
                                                    "onUpdate:modelValue": $event => ((pair.remote) = $event),
                                                    label: "MP映射目录",
                                                    density: "compact"
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue"])
                                                ]),
                                                _createVNode(_component_v_btn, {
                                                  icon: "",
                                                  size: "small",
                                                  color: "error",
                                                  class: "ml-2",
                                                  onClick: $event => (removePath(index, 'mp'))
                                                }, {
                                                  default: _withCtx(() => [
                                                    _createVNode(_component_v_icon, null, {
                                                      default: _withCtx(() => _cache[53] || (_cache[53] = [
                                                        _createTextVNode("mdi-delete")
                                                      ])),
                                                      _: 1
                                                    })
                                                  ]),
                                                  _: 2
                                                }, 1032, ["onClick"])
                                              ]))
                                            }), 128)),
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              "prepend-icon": "mdi-plus",
                                              variant: "outlined",
                                              class: "mt-2 align-self-start",
                                              onClick: _cache[12] || (_cache[12] = $event => (addPath('mp')))
                                            }, {
                                              default: _withCtx(() => _cache[54] || (_cache[54] = [
                                                _createTextVNode(" 添加路径 ")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _createVNode(_component_v_alert, {
                                            type: "info",
                                            variant: "tonal",
                                            density: "compact",
                                            class: "mt-2"
                                          }, {
                                            default: _withCtx(() => _cache[55] || (_cache[55] = [
                                              _createTextVNode(" 媒体服务器映射路径和MP映射路径不一样时请配置此项，如果不配置则无法正常刷新。"),
                                              _createElementVNode("br", null, null, -1),
                                              _createTextVNode(" 当映射路径一样时可省略此配置。 ")
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
                          _createVNode(_component_v_window_item, { value: "tab-sync" }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_card_text, null, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_row, null, {
                                    default: _withCtx(() => [
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "4"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.timing_full_sync_strm,
                                            "onUpdate:modelValue": _cache[13] || (_cache[13] = $event => ((config.timing_full_sync_strm) = $event)),
                                            label: "定期全量同步",
                                            color: "info"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "4"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_VCronField, {
                                            modelValue: config.cron_full_sync_strm,
                                            "onUpdate:modelValue": _cache[14] || (_cache[14] = $event => ((config.cron_full_sync_strm) = $event)),
                                            label: "运行全量同步周期",
                                            hint: "设置全量同步的执行周期",
                                            "persistent-hint": "",
                                            density: "compact"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "4"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.full_sync_auto_download_mediainfo_enabled,
                                            "onUpdate:modelValue": _cache[15] || (_cache[15] = $event => ((config.full_sync_auto_download_mediainfo_enabled) = $event)),
                                            label: "下载媒体数据文件",
                                            color: "warning"
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
                                          _createElementVNode("div", _hoisted_9, [
                                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(fullSyncPaths.value, (pair, index) => {
                                              return (_openBlock(), _createElementBlock("div", {
                                                key: `full-${index}`,
                                                class: "mb-2 d-flex align-center"
                                              }, [
                                                _createElementVNode("div", _hoisted_10, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.local,
                                                    "onUpdate:modelValue": $event => ((pair.local) = $event),
                                                    label: "本地STRM目录",
                                                    density: "compact",
                                                    "append-icon": "mdi-folder",
                                                    "onClick:append": $event => (openDirSelector(index, 'local', 'fullSync'))
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue", "onClick:append"])
                                                ]),
                                                _createVNode(_component_v_icon, null, {
                                                  default: _withCtx(() => _cache[56] || (_cache[56] = [
                                                    _createTextVNode("mdi-pound")
                                                  ])),
                                                  _: 1
                                                }),
                                                _createElementVNode("div", _hoisted_11, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.remote,
                                                    "onUpdate:modelValue": $event => ((pair.remote) = $event),
                                                    label: "网盘媒体库目录",
                                                    density: "compact",
                                                    "append-icon": "mdi-folder-network",
                                                    "onClick:append": $event => (openDirSelector(index, 'remote', 'fullSync'))
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue", "onClick:append"])
                                                ]),
                                                _createVNode(_component_v_btn, {
                                                  icon: "",
                                                  size: "small",
                                                  color: "error",
                                                  class: "ml-2",
                                                  onClick: $event => (removePath(index, 'fullSync'))
                                                }, {
                                                  default: _withCtx(() => [
                                                    _createVNode(_component_v_icon, null, {
                                                      default: _withCtx(() => _cache[57] || (_cache[57] = [
                                                        _createTextVNode("mdi-delete")
                                                      ])),
                                                      _: 1
                                                    })
                                                  ]),
                                                  _: 2
                                                }, 1032, ["onClick"])
                                              ]))
                                            }), 128)),
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              "prepend-icon": "mdi-plus",
                                              variant: "outlined",
                                              class: "mt-2 align-self-start",
                                              onClick: _cache[16] || (_cache[16] = $event => (addPath('fullSync')))
                                            }, {
                                              default: _withCtx(() => _cache[58] || (_cache[58] = [
                                                _createTextVNode(" 添加路径 ")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _createVNode(_component_v_alert, {
                                            type: "info",
                                            variant: "tonal",
                                            density: "compact",
                                            class: "mt-2"
                                          }, {
                                            default: _withCtx(() => _cache[59] || (_cache[59] = [
                                              _createTextVNode(" 全量扫描配置的网盘目录，并在对应的本地目录生成STRM文件。"),
                                              _createElementVNode("br", null, null, -1),
                                              _createTextVNode(" 格式：本地STRM目录#网盘媒体库目录 ")
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
                          _createVNode(_component_v_window_item, { value: "tab-life" }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_card_text, null, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_row, null, {
                                    default: _withCtx(() => [
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.monitor_life_enabled,
                                            "onUpdate:modelValue": _cache[17] || (_cache[17] = $event => ((config.monitor_life_enabled) = $event)),
                                            label: "监控115生活事件",
                                            color: "info"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.monitor_life_auto_download_mediainfo_enabled,
                                            "onUpdate:modelValue": _cache[18] || (_cache[18] = $event => ((config.monitor_life_auto_download_mediainfo_enabled) = $event)),
                                            label: "下载媒体数据文件",
                                            color: "warning"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.monitor_life_scrape_metadata_enabled,
                                            "onUpdate:modelValue": _cache[19] || (_cache[19] = $event => ((config.monitor_life_scrape_metadata_enabled) = $event)),
                                            label: "STRM自动刮削",
                                            color: "primary"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.monitor_life_auto_remove_local_enabled,
                                            "onUpdate:modelValue": _cache[20] || (_cache[20] = $event => ((config.monitor_life_auto_remove_local_enabled) = $event)),
                                            label: "网盘删除本地同步删除",
                                            color: "error"
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
                                        md: "4"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.monitor_life_media_server_refresh_enabled,
                                            "onUpdate:modelValue": _cache[21] || (_cache[21] = $event => ((config.monitor_life_media_server_refresh_enabled) = $event)),
                                            label: "媒体服务器刷新",
                                            color: "warning"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "8"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_select, {
                                            modelValue: config.monitor_life_mediaservers,
                                            "onUpdate:modelValue": _cache[22] || (_cache[22] = $event => ((config.monitor_life_mediaservers) = $event)),
                                            label: "媒体服务器",
                                            items: mediaservers.value,
                                            multiple: "",
                                            chips: "",
                                            "closable-chips": ""
                                          }, null, 8, ["modelValue", "items"])
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
                                          _createElementVNode("div", _hoisted_12, [
                                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(monitorLifePaths.value, (pair, index) => {
                                              return (_openBlock(), _createElementBlock("div", {
                                                key: `life-${index}`,
                                                class: "mb-2 d-flex align-center"
                                              }, [
                                                _createElementVNode("div", _hoisted_13, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.local,
                                                    "onUpdate:modelValue": $event => ((pair.local) = $event),
                                                    label: "本地STRM目录",
                                                    density: "compact",
                                                    "append-icon": "mdi-folder",
                                                    "onClick:append": $event => (openDirSelector(index, 'local', 'monitorLife'))
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue", "onClick:append"])
                                                ]),
                                                _createVNode(_component_v_icon, null, {
                                                  default: _withCtx(() => _cache[60] || (_cache[60] = [
                                                    _createTextVNode("mdi-pound")
                                                  ])),
                                                  _: 1
                                                }),
                                                _createElementVNode("div", _hoisted_14, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.remote,
                                                    "onUpdate:modelValue": $event => ((pair.remote) = $event),
                                                    label: "网盘媒体库目录",
                                                    density: "compact",
                                                    "append-icon": "mdi-folder-network",
                                                    "onClick:append": $event => (openDirSelector(index, 'remote', 'monitorLife'))
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue", "onClick:append"])
                                                ]),
                                                _createVNode(_component_v_btn, {
                                                  icon: "",
                                                  size: "small",
                                                  color: "error",
                                                  class: "ml-2",
                                                  onClick: $event => (removePath(index, 'monitorLife'))
                                                }, {
                                                  default: _withCtx(() => [
                                                    _createVNode(_component_v_icon, null, {
                                                      default: _withCtx(() => _cache[61] || (_cache[61] = [
                                                        _createTextVNode("mdi-delete")
                                                      ])),
                                                      _: 1
                                                    })
                                                  ]),
                                                  _: 2
                                                }, 1032, ["onClick"])
                                              ]))
                                            }), 128)),
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              "prepend-icon": "mdi-plus",
                                              variant: "outlined",
                                              class: "mt-2 align-self-start",
                                              onClick: _cache[23] || (_cache[23] = $event => (addPath('monitorLife')))
                                            }, {
                                              default: _withCtx(() => _cache[62] || (_cache[62] = [
                                                _createTextVNode(" 添加路径 ")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _createVNode(_component_v_alert, {
                                            type: "info",
                                            variant: "tonal",
                                            density: "compact",
                                            class: "mt-2"
                                          }, {
                                            default: _withCtx(() => _cache[63] || (_cache[63] = [
                                              _createTextVNode(" 监控115生活（上传、移动、接收文件、删除、复制）事件，自动在此处配置的本地目录生成对应的STRM文件或删除。"),
                                              _createElementVNode("br", null, null, -1),
                                              _createTextVNode(" 格式：本地STRM目录#网盘媒体库目录 ")
                                            ])),
                                            _: 1
                                          })
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
                                          _createElementVNode("div", _hoisted_15, [
                                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(monitorLifeMpPaths.value, (pair, index) => {
                                              return (_openBlock(), _createElementBlock("div", {
                                                key: `life-mp-${index}`,
                                                class: "mb-2 d-flex align-center"
                                              }, [
                                                _createElementVNode("div", _hoisted_16, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.local,
                                                    "onUpdate:modelValue": $event => ((pair.local) = $event),
                                                    label: "媒体库服务器映射目录",
                                                    density: "compact"
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue"])
                                                ]),
                                                _createVNode(_component_v_icon, null, {
                                                  default: _withCtx(() => _cache[64] || (_cache[64] = [
                                                    _createTextVNode("mdi-pound")
                                                  ])),
                                                  _: 1
                                                }),
                                                _createElementVNode("div", _hoisted_17, [
                                                  _createVNode(_component_v_text_field, {
                                                    modelValue: pair.remote,
                                                    "onUpdate:modelValue": $event => ((pair.remote) = $event),
                                                    label: "MP映射目录",
                                                    density: "compact"
                                                  }, null, 8, ["modelValue", "onUpdate:modelValue"])
                                                ]),
                                                _createVNode(_component_v_btn, {
                                                  icon: "",
                                                  size: "small",
                                                  color: "error",
                                                  class: "ml-2",
                                                  onClick: $event => (removePath(index, 'monitorLifeMp'))
                                                }, {
                                                  default: _withCtx(() => [
                                                    _createVNode(_component_v_icon, null, {
                                                      default: _withCtx(() => _cache[65] || (_cache[65] = [
                                                        _createTextVNode("mdi-delete")
                                                      ])),
                                                      _: 1
                                                    })
                                                  ]),
                                                  _: 2
                                                }, 1032, ["onClick"])
                                              ]))
                                            }), 128)),
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              "prepend-icon": "mdi-plus",
                                              variant: "outlined",
                                              class: "mt-2 align-self-start",
                                              onClick: _cache[24] || (_cache[24] = $event => (addPath('monitorLifeMp')))
                                            }, {
                                              default: _withCtx(() => _cache[66] || (_cache[66] = [
                                                _createTextVNode(" 添加路径 ")
                                              ])),
                                              _: 1
                                            })
                                          ]),
                                          _createVNode(_component_v_alert, {
                                            type: "info",
                                            variant: "tonal",
                                            density: "compact",
                                            class: "mt-2"
                                          }, {
                                            default: _withCtx(() => _cache[67] || (_cache[67] = [
                                              _createTextVNode(" 媒体服务器映射路径和MP映射路径不一样时请配置此项，如果不配置则无法正常刷新。"),
                                              _createElementVNode("br", null, null, -1),
                                              _createTextVNode(" 当映射路径一样时可省略此配置。 ")
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
                          _createVNode(_component_v_window_item, { value: "tab-cleanup" }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_card_text, null, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_alert, {
                                    type: "warning",
                                    variant: "tonal",
                                    density: "compact",
                                    class: "mb-4"
                                  }, {
                                    default: _withCtx(() => _cache[68] || (_cache[68] = [
                                      _createTextVNode(" 注意，清空 回收站/我的接收 后文件不可恢复，如果产生重要数据丢失本程序不负责！ ")
                                    ])),
                                    _: 1
                                  }),
                                  _createVNode(_component_v_row, null, {
                                    default: _withCtx(() => [
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.clear_recyclebin_enabled,
                                            "onUpdate:modelValue": _cache[25] || (_cache[25] = $event => ((config.clear_recyclebin_enabled) = $event)),
                                            label: "清空回收站",
                                            color: "error"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.clear_receive_path_enabled,
                                            "onUpdate:modelValue": _cache[26] || (_cache[26] = $event => ((config.clear_receive_path_enabled) = $event)),
                                            label: "清空我的接收目录",
                                            color: "error"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_text_field, {
                                            modelValue: config.password,
                                            "onUpdate:modelValue": _cache[27] || (_cache[27] = $event => ((config.password) = $event)),
                                            label: "115访问密码",
                                            hint: "115网盘登录密码",
                                            "persistent-hint": "",
                                            type: "password",
                                            density: "compact",
                                            variant: "outlined",
                                            "hide-details": "auto"
                                          }, null, 8, ["modelValue"])
                                        ]),
                                        _: 1
                                      }),
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "3"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_VCronField, {
                                            modelValue: config.cron_clear,
                                            "onUpdate:modelValue": _cache[28] || (_cache[28] = $event => ((config.cron_clear) = $event)),
                                            label: "清理周期",
                                            hint: "设置清理任务的执行周期",
                                            "persistent-hint": "",
                                            density: "compact"
                                          }, null, 8, ["modelValue"])
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
                          _createVNode(_component_v_window_item, { value: "tab-pan-transfer" }, {
                            default: _withCtx(() => [
                              _createVNode(_component_v_card_text, null, {
                                default: _withCtx(() => [
                                  _createVNode(_component_v_row, null, {
                                    default: _withCtx(() => [
                                      _createVNode(_component_v_col, {
                                        cols: "12",
                                        md: "4"
                                      }, {
                                        default: _withCtx(() => [
                                          _createVNode(_component_v_switch, {
                                            modelValue: config.pan_transfer_enabled,
                                            "onUpdate:modelValue": _cache[29] || (_cache[29] = $event => ((config.pan_transfer_enabled) = $event)),
                                            label: "网盘整理",
                                            color: "info"
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
                                          _createElementVNode("div", _hoisted_18, [
                                            (_openBlock(true), _createElementBlock(_Fragment, null, _renderList(panTransferPaths.value, (path, index) => {
                                              return (_openBlock(), _createElementBlock("div", {
                                                key: `pan-${index}`,
                                                class: "mb-2 d-flex align-center"
                                              }, [
                                                _createVNode(_component_v_text_field, {
                                                  modelValue: path.path,
                                                  "onUpdate:modelValue": $event => ((path.path) = $event),
                                                  label: "网盘待整理目录",
                                                  density: "compact",
                                                  "append-icon": "mdi-folder-network",
                                                  "onClick:append": $event => (openDirSelector(index, 'remote', 'panTransfer')),
                                                  class: "flex-grow-1"
                                                }, null, 8, ["modelValue", "onUpdate:modelValue", "onClick:append"]),
                                                _createVNode(_component_v_btn, {
                                                  icon: "",
                                                  size: "small",
                                                  color: "error",
                                                  class: "ml-2",
                                                  onClick: $event => (removePanTransferPath(index))
                                                }, {
                                                  default: _withCtx(() => [
                                                    _createVNode(_component_v_icon, null, {
                                                      default: _withCtx(() => _cache[69] || (_cache[69] = [
                                                        _createTextVNode("mdi-delete")
                                                      ])),
                                                      _: 1
                                                    })
                                                  ]),
                                                  _: 2
                                                }, 1032, ["onClick"])
                                              ]))
                                            }), 128)),
                                            _createVNode(_component_v_btn, {
                                              size: "small",
                                              "prepend-icon": "mdi-plus",
                                              variant: "outlined",
                                              class: "mt-2 align-self-start",
                                              onClick: addPanTransferPath
                                            }, {
                                              default: _withCtx(() => _cache[70] || (_cache[70] = [
                                                _createTextVNode(" 添加路径 ")
                                              ])),
                                              _: 1
                                            })
                                          ])
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
                                    class: "mt-2"
                                  }, {
                                    default: _withCtx(() => _cache[71] || (_cache[71] = [
                                      _createTextVNode(" 使用本功能需要先进入 设定-目录 进行配置："),
                                      _createElementVNode("br", null, null, -1),
                                      _createTextVNode(" 1. 添加目录配置卡，按需配置媒体类型和媒体类别，资源存储选择115网盘，资源目录输入网盘待整理文件夹"),
                                      _createElementVNode("br", null, null, -1),
                                      _createTextVNode(" 2. 自动整理模式选择手动整理，媒体库存储依旧选择115网盘，并配置好媒体库路径，整理方式选择移动，按需配置分类、重命名、通知"),
                                      _createElementVNode("br", null, null, -1),
                                      _createTextVNode(" 3. 配置完成目录设置后只需要在上方 网盘待整理目录 填入 网盘待整理文件夹 即可"),
                                      _createElementVNode("br", null, null, -1)
                                    ])),
                                    _: 1
                                  }),
                                  _createVNode(_component_v_alert, {
                                    type: "warning",
                                    variant: "tonal",
                                    density: "compact",
                                    class: "mt-2"
                                  }, {
                                    default: _withCtx(() => _cache[72] || (_cache[72] = [
                                      _createTextVNode(" 注意：配置目录时不能选择刮削元数据，否则可能导致风控！ ")
                                    ])),
                                    _: 1
                                  }),
                                  _createVNode(_component_v_alert, {
                                    type: "warning",
                                    variant: "tonal",
                                    density: "compact",
                                    class: "mt-2"
                                  }, {
                                    default: _withCtx(() => _cache[73] || (_cache[73] = [
                                      _createTextVNode(" 注意：115生活事件监控会忽略网盘整理触发的移动事件，所以必须使用MP整理事件监控生成STRM ")
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
                    ]),
                    _: 1
                  })
                ]))
          ]),
          _: 1
        }),
        _createVNode(_component_v_card_actions, {
          class: "px-3 py-2 d-flex",
          style: {"flex-shrink":"0"}
        }, {
          default: _withCtx(() => [
            _createVNode(_component_v_btn, {
              color: "warning",
              variant: "text",
              onClick: _cache[31] || (_cache[31] = $event => (emit('switch'))),
              size: "small",
              "prepend-icon": "mdi-arrow-left"
            }, {
              default: _withCtx(() => _cache[74] || (_cache[74] = [
                _createTextVNode(" 返回 ")
              ])),
              _: 1
            }),
            _createVNode(_component_v_spacer),
            _createVNode(_component_v_btn, {
              color: "warning",
              variant: "text",
              onClick: triggerFullSync,
              loading: syncLoading.value,
              size: "small",
              "prepend-icon": "mdi-sync"
            }, {
              default: _withCtx(() => _cache[75] || (_cache[75] = [
                _createTextVNode(" 全量同步 ")
              ])),
              _: 1
            }, 8, ["loading"]),
            _createVNode(_component_v_btn, {
              color: "success",
              variant: "text",
              onClick: saveConfig,
              loading: saveLoading.value,
              size: "small",
              "prepend-icon": "mdi-content-save"
            }, {
              default: _withCtx(() => _cache[76] || (_cache[76] = [
                _createTextVNode(" 保存配置 ")
              ])),
              _: 1
            }, 8, ["loading"])
          ]),
          _: 1
        })
      ]),
      _: 1
    }),
    _createVNode(_component_v_dialog, {
      modelValue: dirDialog.show,
      "onUpdate:modelValue": _cache[33] || (_cache[33] = $event => ((dirDialog.show) = $event)),
      "max-width": "800"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card, null, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_title, { class: "text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5" }, {
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
                  ? (_openBlock(), _createElementBlock("div", _hoisted_19, [
                      _createVNode(_component_v_progress_circular, {
                        indeterminate: "",
                        color: "primary"
                      })
                    ]))
                  : (_openBlock(), _createElementBlock("div", _hoisted_20, [
                      _createVNode(_component_v_text_field, {
                        modelValue: dirDialog.currentPath,
                        "onUpdate:modelValue": _cache[32] || (_cache[32] = $event => ((dirDialog.currentPath) = $event)),
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
                                class: "py-1"
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
                                    default: _withCtx(() => _cache[77] || (_cache[77] = [
                                      _createTextVNode("上级目录")
                                    ])),
                                    _: 1
                                  }),
                                  _createVNode(_component_v_list_item_subtitle, null, {
                                    default: _withCtx(() => _cache[78] || (_cache[78] = [
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
                              class: "py-1"
                            }, {
                              prepend: _withCtx(() => [
                                _createVNode(_component_v_icon, {
                                  icon: item.is_dir ? 'mdi-folder' : 'mdi-file',
                                  size: "small",
                                  class: "mr-2",
                                  color: item.is_dir ? 'amber-darken-2' : 'blue'
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
                                    default: _withCtx(() => _cache[79] || (_cache[79] = [
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
            _createVNode(_component_v_card_actions, { class: "px-3 py-2" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  color: "primary",
                  onClick: confirmDirSelection,
                  disabled: !dirDialog.currentPath || dirDialog.loading,
                  variant: "text",
                  size: "small"
                }, {
                  default: _withCtx(() => _cache[80] || (_cache[80] = [
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
                  default: _withCtx(() => _cache[81] || (_cache[81] = [
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
    }, 8, ["modelValue"]),
    _createVNode(_component_v_dialog, {
      modelValue: qrDialog.show,
      "onUpdate:modelValue": _cache[35] || (_cache[35] = $event => ((qrDialog.show) = $event)),
      "max-width": "450"
    }, {
      default: _withCtx(() => [
        _createVNode(_component_v_card, null, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_title, { class: "text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_icon, {
                  icon: "mdi-qrcode",
                  class: "mr-2",
                  color: "primary",
                  size: "small"
                }),
                _cache[82] || (_cache[82] = _createElementVNode("span", null, "115网盘扫码登录", -1))
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_text, { class: "text-center py-4" }, {
              default: _withCtx(() => [
                (qrDialog.error)
                  ? (_openBlock(), _createBlock(_component_v_alert, {
                      key: 0,
                      type: "error",
                      density: "compact",
                      class: "mb-3 mx-3",
                      variant: "tonal",
                      closable: ""
                    }, {
                      default: _withCtx(() => [
                        _createTextVNode(_toDisplayString(qrDialog.error), 1)
                      ]),
                      _: 1
                    }))
                  : _createCommentVNode("", true),
                (qrDialog.loading)
                  ? (_openBlock(), _createElementBlock("div", _hoisted_21, [
                      _createVNode(_component_v_progress_circular, {
                        indeterminate: "",
                        color: "primary",
                        class: "mb-3"
                      }),
                      _cache[83] || (_cache[83] = _createElementVNode("div", null, "正在获取二维码...", -1))
                    ]))
                  : (qrDialog.qrcode)
                    ? (_openBlock(), _createElementBlock("div", _hoisted_22, [
                        _cache[86] || (_cache[86] = _createElementVNode("div", { class: "mb-2 font-weight-medium" }, "请选择扫码方式", -1)),
                        _createVNode(_component_v_chip_group, {
                          modelValue: qrDialog.clientType,
                          "onUpdate:modelValue": _cache[34] || (_cache[34] = $event => ((qrDialog.clientType) = $event)),
                          class: "mb-3",
                          mandatory: "",
                          "selected-class": "primary"
                        }, {
                          default: _withCtx(() => [
                            (_openBlock(), _createElementBlock(_Fragment, null, _renderList(clientTypes, (type) => {
                              return _createVNode(_component_v_chip, {
                                key: type.value,
                                value: type.value,
                                variant: "outlined",
                                color: "primary",
                                size: "small"
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(type.label), 1)
                                ]),
                                _: 2
                              }, 1032, ["value"])
                            }), 64))
                          ]),
                          _: 1
                        }, 8, ["modelValue"]),
                        _createElementVNode("div", _hoisted_23, [
                          _createVNode(_component_v_card, {
                            flat: "",
                            class: "border pa-2 mb-2"
                          }, {
                            default: _withCtx(() => [
                              _createElementVNode("img", {
                                src: qrDialog.qrcode,
                                width: "220",
                                height: "220"
                              }, null, 8, _hoisted_24)
                            ]),
                            _: 1
                          }),
                          _createElementVNode("div", _hoisted_25, _toDisplayString(qrDialog.tips), 1),
                          _createElementVNode("div", _hoisted_26, _toDisplayString(qrDialog.status), 1)
                        ]),
                        _createVNode(_component_v_btn, {
                          color: "primary",
                          variant: "tonal",
                          onClick: refreshQrCode,
                          size: "small",
                          class: "mb-2"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_icon, {
                              left: "",
                              size: "small",
                              class: "mr-1"
                            }, {
                              default: _withCtx(() => _cache[84] || (_cache[84] = [
                                _createTextVNode("mdi-refresh")
                              ])),
                              _: 1
                            }),
                            _cache[85] || (_cache[85] = _createTextVNode("刷新二维码 "))
                          ]),
                          _: 1
                        })
                      ]))
                    : (_openBlock(), _createElementBlock("div", _hoisted_27, [
                        _createVNode(_component_v_icon, {
                          icon: "mdi-qrcode-off",
                          size: "64",
                          color: "grey",
                          class: "mb-3"
                        }),
                        _cache[88] || (_cache[88] = _createElementVNode("div", { class: "text-subtitle-1" }, "二维码获取失败", -1)),
                        _cache[89] || (_cache[89] = _createElementVNode("div", { class: "text-body-2 text-grey" }, "请点击刷新按钮重试", -1)),
                        _createElementVNode("div", _hoisted_28, [
                          _createVNode(_component_v_icon, {
                            icon: "mdi-alert-circle",
                            size: "small",
                            class: "mr-1 text-warning"
                          }),
                          _cache[87] || (_cache[87] = _createTextVNode(" 如果多次获取失败，请检查网络连接 "))
                        ])
                      ]))
              ]),
              _: 1
            }),
            _createVNode(_component_v_divider),
            _createVNode(_component_v_card_actions, { class: "px-3 py-2" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_btn, {
                  color: "grey",
                  variant: "text",
                  onClick: closeQrDialog,
                  size: "small",
                  "prepend-icon": "mdi-close"
                }, {
                  default: _withCtx(() => _cache[90] || (_cache[90] = [
                    _createTextVNode("关闭")
                  ])),
                  _: 1
                }),
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  color: "primary",
                  variant: "text",
                  onClick: refreshQrCode,
                  disabled: qrDialog.loading,
                  size: "small",
                  "prepend-icon": "mdi-refresh"
                }, {
                  default: _withCtx(() => _cache[91] || (_cache[91] = [
                    _createTextVNode(" 刷新二维码 ")
                  ])),
                  _: 1
                }, 8, ["disabled"])
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]),
      _: 1
    }, 8, ["modelValue"])
  ]))
}
}

};
const Config = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-bfd264b1"]]);

export { Config as default };
