import { importShared } from './__federation_fn_import-JrT3xvdd.js';
import { _ as _export_sfc } from './_plugin-vue_export-helper-pcqpp-6-.js';

const {toDisplayString:_toDisplayString,createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createElementBlock:_createElementBlock,normalizeClass:_normalizeClass,createElementVNode:_createElementVNode} = await importShared('vue');


const _hoisted_1 = { class: "dashboard-widget" };
const _hoisted_2 = {
  key: 0,
  class: "text-center py-2"
};
const _hoisted_3 = {
  key: 1,
  class: "text-error text-caption d-flex align-center"
};
const _hoisted_4 = { key: 2 };
const _hoisted_5 = {
  key: 3,
  class: "text-caption text-disabled text-center py-2"
};
const _hoisted_6 = { class: "text-caption text-disabled" };

const {ref,reactive,onMounted,onUnmounted,computed} = await importShared('vue');


// 接收props

const _sfc_main = {
  __name: 'Dashboard',
  props: {
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
},
  setup(__props) {

const props = __props;

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

return (_ctx, _cache) => {
  const _component_v_card_title = _resolveComponent("v-card-title");
  const _component_v_card_subtitle = _resolveComponent("v-card-subtitle");
  const _component_v_card_item = _resolveComponent("v-card-item");
  const _component_v_progress_circular = _resolveComponent("v-progress-circular");
  const _component_v_icon = _resolveComponent("v-icon");
  const _component_v_list_item_title = _resolveComponent("v-list-item-title");
  const _component_v_list_item = _resolveComponent("v-list-item");
  const _component_v_list = _resolveComponent("v-list");
  const _component_v_card_text = _resolveComponent("v-card-text");
  const _component_v_divider = _resolveComponent("v-divider");
  const _component_v_spacer = _resolveComponent("v-spacer");
  const _component_v_btn = _resolveComponent("v-btn");
  const _component_v_card_actions = _resolveComponent("v-card-actions");
  const _component_v_card = _resolveComponent("v-card");

  return (_openBlock(), _createElementBlock("div", _hoisted_1, [
    _createVNode(_component_v_card, {
      flat: !props.config?.attrs?.border,
      loading: loading.value,
      class: "fill-height d-flex flex-column"
    }, {
      default: _withCtx(() => [
        (props.config?.attrs?.title || props.config?.attrs?.subtitle)
          ? (_openBlock(), _createBlock(_component_v_card_item, { key: 0 }, {
              default: _withCtx(() => [
                _createVNode(_component_v_card_title, null, {
                  default: _withCtx(() => [
                    _createTextVNode(_toDisplayString(props.config?.attrs?.title || '115网盘STRM助手'), 1)
                  ]),
                  _: 1
                }),
                (props.config?.attrs?.subtitle)
                  ? (_openBlock(), _createBlock(_component_v_card_subtitle, { key: 0 }, {
                      default: _withCtx(() => [
                        _createTextVNode(_toDisplayString(props.config.attrs.subtitle), 1)
                      ]),
                      _: 1
                    }))
                  : _createCommentVNode("", true)
              ]),
              _: 1
            }))
          : _createCommentVNode("", true),
        _createVNode(_component_v_card_text, { class: "flex-grow-1 pa-3" }, {
          default: _withCtx(() => [
            (loading.value && !initialDataLoaded.value)
              ? (_openBlock(), _createElementBlock("div", _hoisted_2, [
                  _createVNode(_component_v_progress_circular, {
                    indeterminate: "",
                    color: "primary",
                    size: "small"
                  })
                ]))
              : (error.value)
                ? (_openBlock(), _createElementBlock("div", _hoisted_3, [
                    _createVNode(_component_v_icon, {
                      size: "small",
                      color: "error",
                      class: "mr-1"
                    }, {
                      default: _withCtx(() => _cache[0] || (_cache[0] = [
                        _createTextVNode("mdi-alert-circle-outline")
                      ])),
                      _: 1
                    }),
                    _createTextVNode(" " + _toDisplayString(error.value || '数据加载失败'), 1)
                  ]))
                : (initialDataLoaded.value)
                  ? (_openBlock(), _createElementBlock("div", _hoisted_4, [
                      _createVNode(_component_v_list, {
                        density: "compact",
                        class: "py-0"
                      }, {
                        default: _withCtx(() => [
                          _createVNode(_component_v_list_item, { class: "pa-0" }, {
                            prepend: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                color: status.enabled ? 'success' : 'grey',
                                class: "mr-2"
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(status.enabled ? 'mdi-check-circle' : 'mdi-close-circle'), 1)
                                ]),
                                _: 1
                              }, 8, ["color"])
                            ]),
                            default: _withCtx(() => [
                              _createVNode(_component_v_list_item_title, { class: "text-caption" }, {
                                default: _withCtx(() => [
                                  _cache[1] || (_cache[1] = _createTextVNode(" 插件状态: ")),
                                  _createElementVNode("span", {
                                    class: _normalizeClass(status.enabled ? 'text-success' : 'text-grey')
                                  }, _toDisplayString(status.enabled ? '已启用' : '已禁用'), 3)
                                ]),
                                _: 1
                              })
                            ]),
                            _: 1
                          }),
                          _createVNode(_component_v_list_item, { class: "pa-0" }, {
                            prepend: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                color: status.has_client ? 'success' : 'error',
                                class: "mr-2"
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(status.has_client ? 'mdi-account-check' : 'mdi-account-off'), 1)
                                ]),
                                _: 1
                              }, 8, ["color"])
                            ]),
                            default: _withCtx(() => [
                              _createVNode(_component_v_list_item_title, { class: "text-caption" }, {
                                default: _withCtx(() => [
                                  _cache[2] || (_cache[2] = _createTextVNode(" 115客户端: ")),
                                  _createElementVNode("span", {
                                    class: _normalizeClass(status.has_client ? 'text-success' : 'text-error')
                                  }, _toDisplayString(status.has_client ? '已连接' : '未连接'), 3)
                                ]),
                                _: 1
                              })
                            ]),
                            _: 1
                          }),
                          _createVNode(_component_v_list_item, { class: "pa-0" }, {
                            prepend: _withCtx(() => [
                              _createVNode(_component_v_icon, {
                                size: "small",
                                color: status.running ? 'success' : 'grey',
                                class: "mr-2"
                              }, {
                                default: _withCtx(() => [
                                  _createTextVNode(_toDisplayString(status.running ? 'mdi-play-circle' : 'mdi-pause-circle'), 1)
                                ]),
                                _: 1
                              }, 8, ["color"])
                            ]),
                            default: _withCtx(() => [
                              _createVNode(_component_v_list_item_title, { class: "text-caption" }, {
                                default: _withCtx(() => [
                                  _cache[3] || (_cache[3] = _createTextVNode(" 任务状态: ")),
                                  _createElementVNode("span", {
                                    class: _normalizeClass(status.running ? 'text-success' : 'text-grey')
                                  }, _toDisplayString(status.running ? '运行中' : '空闲'), 3)
                                ]),
                                _: 1
                              })
                            ]),
                            _: 1
                          })
                        ]),
                        _: 1
                      })
                    ]))
                  : (_openBlock(), _createElementBlock("div", _hoisted_5, " 暂无数据 "))
          ]),
          _: 1
        }),
        (props.allowRefresh)
          ? (_openBlock(), _createBlock(_component_v_divider, { key: 1 }))
          : _createCommentVNode("", true),
        (props.allowRefresh)
          ? (_openBlock(), _createBlock(_component_v_card_actions, {
              key: 2,
              class: "px-3 py-1"
            }, {
              default: _withCtx(() => [
                _createElementVNode("span", _hoisted_6, _toDisplayString(lastRefreshedTimeDisplay.value), 1),
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  icon: "",
                  variant: "text",
                  size: "small",
                  onClick: fetchData,
                  loading: loading.value
                }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_icon, { size: "small" }, {
                      default: _withCtx(() => _cache[4] || (_cache[4] = [
                        _createTextVNode("mdi-refresh")
                      ])),
                      _: 1
                    })
                  ]),
                  _: 1
                }, 8, ["loading"])
              ]),
              _: 1
            }))
          : _createCommentVNode("", true)
      ]),
      _: 1
    }, 8, ["flat", "loading"])
  ]))
}
}

};
const Dashboard = /*#__PURE__*/_export_sfc(_sfc_main, [['__scopeId',"data-v-3a59a36c"]]);

export { Dashboard as default };
