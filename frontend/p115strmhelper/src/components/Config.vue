<template>
  <div class="plugin-config">
    <v-card flat class="rounded border config-main-card" style="display: flex; flex-direction: column;">
      <!-- 标题区域 -->
      <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5">
        <v-icon icon="mdi-cog" class="mr-2" color="primary" size="small" />
        <span>115网盘STRM助手配置</span>
      </v-card-title>

      <!-- 通知区域 -->
      <v-card-text class="px-3 py-2" style="flex-grow: 1; overflow-y: auto; padding-bottom: 56px;">
        <v-alert v-if="message.text" :type="message.type" density="compact" class="mb-2 text-caption" variant="tonal"
          closable>{{ message.text }}</v-alert>

        <v-skeleton-loader v-if="loading" type="article, actions"></v-skeleton-loader>

        <div v-else class="my-1">
          <!-- 基础设置 -->
          <v-expansion-panels v-model="basicConfigExpanded" variant="tonal" class="mb-3" multiple>
            <v-expansion-panel value="basic-config" class="rounded border config-card" eager>
              <v-expansion-panel-title class="text-subtitle-2 d-flex align-center px-3 py-2 bg-primary-lighten-5">
                <v-icon icon="mdi-cog" class="mr-2" color="primary" size="small" />
                <span>基础设置</span>
              </v-expansion-panel-title>
              <v-expansion-panel-text class="pa-3" eager>
                <v-row>
                  <v-col cols="12" md="4">
                    <v-switch v-model="config.enabled" label="启用插件" color="success" density="compact"></v-switch>
                  </v-col>
                  <v-col cols="12" md="4">
                    <v-select v-model="config.strm_url_format" label="STRM文件URL格式" :items="[
                      { title: 'pickcode', value: 'pickcode' },
                      { title: 'pickcode + name', value: 'pickname' }
                    ]"
                      :hint="config.strm_url_template_enabled ? '已启用自定义模板时优先使用模板，模板渲染失败时将使用此设置作为后备方案' : '选择 STRM 文件的 URL 格式'"
                      persistent-hint chips closable-chips></v-select>
                  </v-col>
                  <v-col cols="12" md="4">
                    <v-select v-model="config.link_redirect_mode" label="直链获取模式" :items="[
                      { title: 'Cookie', value: 'cookie' },
                      { title: 'OpenAPI', value: 'open' }
                    ]" chips closable-chips></v-select>
                  </v-col>
                </v-row>
                <v-row>
                  <v-col cols="12" md="4">
                    <v-switch v-model="config.notify" label="发送通知" color="success" density="compact"></v-switch>
                  </v-col>
                  <v-col cols="12" md="8">
                    <v-select v-model="config.language" label="通知语言" :items="[
                      { title: '简体中文', value: 'zh_CN' },
                      { title: '繁中台湾', value: 'zh_TW' },
                      { title: '繁中港澳', value: 'zh_HK' },
                      { title: '柔情猫娘', value: 'zh_CN_catgirl' },
                      { title: '粤韵风华', value: 'zh_yue' },
                      { title: '咚咚搬砖', value: 'zh_CN_dong' }
                    ]" chips closable-chips></v-select>
                  </v-col>
                </v-row>
                <v-row>
                  <v-col cols="12" md="4">
                    <v-text-field v-model="config.cookies" label="115 Cookie" hint="点击图标切换显隐、复制或扫码" persistent-hint
                      density="compact" variant="outlined" hide-details="auto"
                      :type="isCookieVisible ? 'text' : 'password'">
                      <template v-slot:append-inner>
                        <v-icon :icon="isCookieVisible ? 'mdi-eye-off' : 'mdi-eye'"
                          @click="isCookieVisible = !isCookieVisible"
                          :aria-label="isCookieVisible ? '隐藏Cookie' : '显示Cookie'"
                          :title="isCookieVisible ? '隐藏Cookie' : '显示Cookie'" class="mr-1" size="small"></v-icon>
                        <v-icon icon="mdi-content-copy" @click="copyCookieToClipboard" :disabled="!config.cookies"
                          aria-label="复制Cookie" title="复制Cookie到剪贴板" size="small" class="mr-1"></v-icon>
                      </template>
                      <template v-slot:append>
                        <v-icon icon="mdi-qrcode-scan" @click="openQrCodeDialog"
                          :color="config.cookies ? 'success' : 'default'"
                          :aria-label="config.cookies ? '更新/更换Cookie (重新扫码)' : '扫码获取Cookie'"
                          :title="config.cookies ? '更新/更换Cookie (重新扫码)' : '扫码获取Cookie'"></v-icon>
                      </template>
                    </v-text-field>
                  </v-col>
                  <!-- 阿里云盘 Token 配置 -->
                  <v-col cols="12" md="4">
                    <v-text-field v-model="config.aliyundrive_token" label="阿里云盘 Token (可选)" hint="非必填。点击图标切换显隐、复制或扫码获取"
                      persistent-hint density="compact" variant="outlined" hide-details="auto"
                      :type="isAliTokenVisible ? 'text' : 'password'">
                      <template v-slot:append-inner>
                        <v-icon :icon="isAliTokenVisible ? 'mdi-eye-off' : 'mdi-eye'"
                          @click="isAliTokenVisible = !isAliTokenVisible"
                          :aria-label="isAliTokenVisible ? '隐藏Token' : '显示Token'"
                          :title="isAliTokenVisible ? '隐藏Token' : '显示Token'" class="mr-1" size="small"></v-icon>
                        <v-icon icon="mdi-content-copy" @click="copyAliTokenToClipboard"
                          :disabled="!config.aliyundrive_token" aria-label="复制Token" title="复制Token到剪贴板" size="small"
                          class="mr-1"></v-icon>
                      </template>
                      <template v-slot:append>
                        <v-icon icon="mdi-qrcode-scan" @click="openAliQrCodeDialog"
                          :color="config.aliyundrive_token ? 'success' : 'default'"
                          :aria-label="config.aliyundrive_token ? '更新/更换Token' : '扫码获取Token'"
                          :title="config.aliyundrive_token ? '更新/更换Token' : '扫码获取Token'"></v-icon>
                      </template>
                    </v-text-field>
                  </v-col>
                  <v-col cols="12" md="4">
                    <v-text-field v-model="config.moviepilot_address" label="MoviePilot 内网访问地址" hint="点右侧图标自动填充当前站点地址。"
                      persistent-hint density="compact" variant="outlined" hide-details="auto">
                      <template v-slot:append>
                        <v-icon icon="mdi-web" @click="setMoviePilotAddressToCurrentOrigin" aria-label="使用当前站点地址"
                          title="使用当前站点地址" color="info"></v-icon>
                      </template>
                    </v-text-field>
                  </v-col>
                </v-row>
                <v-row>
                  <v-col cols="12" md="6">
                    <v-text-field v-model="config.user_rmt_mediaext" label="可整理媒体文件扩展名" hint="支持的媒体文件扩展名，多个用逗号分隔"
                      persistent-hint density="compact" variant="outlined" hide-details="auto"></v-text-field>
                  </v-col>
                  <v-col cols="12" md="6">
                    <v-text-field v-model="config.user_download_mediaext" label="可下载媒体数据文件扩展名"
                      hint="下载的字幕等附属文件扩展名，多个用逗号分隔" persistent-hint density="compact" variant="outlined"
                      hide-details="auto"></v-text-field>
                  </v-col>
                </v-row>
              </v-expansion-panel-text>
            </v-expansion-panel>
          </v-expansion-panels>

          <!-- 标签页 -->
          <v-card flat class="rounded mb-3 border config-card">
            <!-- 主分类标签 -->
            <v-tabs v-model="mainCategory" color="primary" class="main-category-tabs" grow>
              <v-tab value="category-strm" class="main-tab">
                <v-icon size="small" start>mdi-file-link</v-icon>STRM同步
              </v-tab>
              <v-tab value="category-pan" class="main-tab">
                <v-icon size="small" start>mdi-cloud</v-icon>网盘管理
              </v-tab>
              <v-tab value="category-other" class="main-tab">
                <v-icon size="small" start>mdi-puzzle</v-icon>其他功能
              </v-tab>
              <v-tab value="category-system" class="main-tab">
                <v-icon size="small" start>mdi-cog</v-icon>系统配置
              </v-tab>
            </v-tabs>
            <v-divider></v-divider>

            <!-- 主分类内容区域 -->
            <v-window v-model="mainCategory" :touch="false">
              <!-- STRM同步分类 -->
              <v-window-item value="category-strm">
                <v-card-text class="pa-0">
                  <!-- 子标签页 -->
                  <v-tabs v-model="strmSubTab" color="primary" class="sub-category-tabs" slider-color="primary">
                    <v-tab value="tab-transfer" class="sub-tab">
                      <v-icon size="small" start>mdi-file-move-outline</v-icon>监控MP整理
                    </v-tab>
                    <v-tab value="tab-sync" class="sub-tab">
                      <v-icon size="small" start>mdi-sync</v-icon>全量同步
                    </v-tab>
                    <v-tab value="tab-increment-sync" class="sub-tab">
                      <v-icon size="small" start>mdi-book-sync</v-icon>增量同步
                    </v-tab>
                    <v-tab value="tab-life" class="sub-tab">
                      <v-icon size="small" start>mdi-calendar-heart</v-icon>监控115生活事件
                    </v-tab>
                    <v-tab value="tab-api-strm" class="sub-tab">
                      <v-icon size="small" start>mdi-api</v-icon>API STRM生成
                    </v-tab>
                  </v-tabs>
                  <v-divider></v-divider>
                  <v-window v-model="strmSubTab" :touch="false">
                    <v-window-item value="tab-transfer">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.transfer_monitor_enabled" label="启用" color="info"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.transfer_monitor_scrape_metadata_enabled" label="STRM自动刮削"
                              color="primary"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.transfer_monitor_media_server_refresh_enabled" label="媒体服务器刷新"
                              color="warning"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-select v-model="config.transfer_monitor_mediaservers" label="媒体服务器" :items="mediaservers"
                              multiple chips closable-chips></v-select>
                          </v-col>
                        </v-row>

                        <!-- Transfer Monitor Exclude Paths -->
                        <v-row v-if="config.transfer_monitor_scrape_metadata_enabled" class="mt-2 mb-2">
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(item, index) in transferExcludePaths" :key="`transfer-exclude-${index}`"
                                class="mb-2 d-flex align-center">
                                <v-text-field v-model="item.path" label="刮削排除目录" density="compact" variant="outlined"
                                  readonly hide-details class="flex-grow-1 mr-2">
                                </v-text-field>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removeExcludePathEntry(index, 'transfer_exclude')" :disabled="!item.path">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-folder-plus-outline" variant="tonal"
                                class="mt-1 align-self-start"
                                @click="openExcludeDirSelector('transfer_monitor_scrape_metadata_exclude_paths')">
                                添加刮削排除目录
                              </v-btn>
                            </div>
                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-caption">此处添加的本地目录，在STRM文件生成后将不会自动触发刮削。</div>
                            </v-alert>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(pair, index) in transferPaths" :key="`transfer-${index}`"
                                class="mb-2 d-flex align-center">
                                <div class="path-selector flex-grow-1 mr-2">
                                  <v-text-field v-model="pair.local" label="本地STRM目录" density="compact"
                                    append-icon="mdi-folder"
                                    @click:append="openDirSelector(index, 'local', 'transfer')"></v-text-field>
                                </div>
                                <v-icon>mdi-pound</v-icon>
                                <div class="path-selector flex-grow-1 ml-2">
                                  <v-text-field v-model="pair.remote" label="网盘媒体库目录" density="compact"
                                    append-icon="mdi-folder-network"
                                    @click:append="openDirSelector(index, 'remote', 'transfer')"></v-text-field>
                                </div>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removePath(index, 'transfer')">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('transfer')">
                                添加路径
                              </v-btn>
                            </div>

                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                              <div class="text-caption">监控MoviePilot整理入库事件，自动在本地对应目录生成STRM文件。</div>
                              <div class="text-body-2 mt-2 mb-1"><strong>配置说明：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">• <strong>本地STRM目录：</strong>本地STRM文件生成路径</div>
                                <div>• <strong>网盘媒体库目录：</strong>需要生成本地STRM文件的网盘媒体库路径</div>
                              </div>
                            </v-alert>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(pair, index) in transferMpPaths" :key="`mp-${index}`"
                                class="mb-2 d-flex align-center">
                                <div class="path-selector flex-grow-1 mr-2">
                                  <v-text-field v-model="pair.local" label="媒体库服务器映射目录"
                                    density="compact"></v-text-field>
                                </div>
                                <v-icon>mdi-pound</v-icon>
                                <div class="path-selector flex-grow-1 ml-2">
                                  <v-text-field v-model="pair.remote" label="MP映射目录" density="compact"></v-text-field>
                                </div>
                                <v-btn icon size="small" color="error" class="ml-2" @click="removePath(index, 'mp')">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('mp')">
                                添加路径
                              </v-btn>
                            </div>

                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>配置说明：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">• 媒体服务器映射路径和MP映射路径不一样时请配置此项，如果不配置则无法正常刷新。</div>
                                <div>• 当映射路径一样时可省略此配置。</div>
                              </div>
                            </v-alert>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-sync">
                      <v-card-text>
                        <!-- 基础配置 -->
                        <div class="basic-config">
                          <v-row>
                            <v-col cols="12" md="3">
                              <v-select v-model="config.full_sync_overwrite_mode" label="覆盖模式" :items="[
                                { title: '总是', value: 'always' },
                                { title: '从不', value: 'never' }
                              ]" chips closable-chips></v-select>
                            </v-col>
                            <v-col cols="12" md="3">
                              <v-switch v-model="config.full_sync_remove_unless_strm" label="清理失效STRM文件"
                                color="warning"></v-switch>
                            </v-col>
                            <v-col cols="12" md="3">
                              <v-switch v-model="config.full_sync_remove_unless_dir" label="清理无效STRM目录" color="warning"
                                :disabled="!config.full_sync_remove_unless_strm"></v-switch>
                            </v-col>
                            <v-col cols="12" md="3">
                              <v-switch v-model="config.full_sync_remove_unless_file" label="清理无效STRM文件关联的媒体信息文件"
                                color="warning" :disabled="!config.full_sync_remove_unless_strm"></v-switch>
                            </v-col>
                          </v-row>

                          <v-row>
                            <v-col cols="12" md="3">
                              <v-switch v-model="config.timing_full_sync_strm" label="定期全量同步" color="info"></v-switch>
                            </v-col>
                            <v-col cols="12" md="3">
                              <VCronField v-model="config.cron_full_sync_strm" label="运行全量同步周期" hint="设置全量同步的执行周期"
                                persistent-hint density="compact"></VCronField>
                            </v-col>
                            <v-col cols="12" md="3">
                              <v-switch v-model="config.full_sync_auto_download_mediainfo_enabled" label="下载媒体数据文件"
                                color="warning"></v-switch>
                            </v-col>
                            <v-col cols="12" md="3">
                              <v-text-field v-model="fullSyncMinFileSizeFormatted" label="STRM最小文件大小"
                                hint="小于此值的文件将不生成STRM(单位K,M,G)" persistent-hint density="compact"
                                placeholder="例如: 100M (可为空)" clearable></v-text-field>
                            </v-col>
                          </v-row>

                          <v-row>
                            <v-col cols="12" md="4">
                              <v-switch v-model="config.full_sync_media_server_refresh_enabled" label="全量同步后刷新媒体库"
                                color="error" density="compact"></v-switch>
                            </v-col>
                            <v-col cols="12" md="8">
                              <v-select v-model="config.full_sync_mediaservers" label="媒体服务器" :items="mediaservers"
                                multiple chips closable-chips :disabled="!config.full_sync_media_server_refresh_enabled"
                                hint="全量同步完成后将刷新整个媒体库，请谨慎使用" persistent-hint></v-select>
                            </v-col>
                            <v-col v-if="config.full_sync_media_server_refresh_enabled" cols="12">
                              <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                                <div class="text-body-2 mb-1"><strong>重要警告</strong></div>
                                <div class="text-caption">
                                  启用此功能后，全量同步完成后将自动刷新整个媒体库。此操作会扫描所有媒体文件，可能导致媒体服务器负载增加，请确保您已了解此风险并自行承担相应责任。
                                </div>
                              </v-alert>
                            </v-col>
                          </v-row>

                          <v-row>
                            <v-col cols="12">
                              <div class="d-flex flex-column">
                                <div v-for="(pair, index) in fullSyncPaths" :key="`full-${index}`"
                                  class="mb-2 d-flex align-center">
                                  <div class="path-selector flex-grow-1 mr-2">
                                    <v-text-field v-model="pair.local" label="本地STRM目录" density="compact"
                                      append-icon="mdi-folder"
                                      @click:append="openDirSelector(index, 'local', 'fullSync')"></v-text-field>
                                  </div>
                                  <v-icon>mdi-pound</v-icon>
                                  <div class="path-selector flex-grow-1 ml-2">
                                    <v-text-field v-model="pair.remote" label="网盘媒体库目录" density="compact"
                                      append-icon="mdi-folder-network"
                                      @click:append="openDirSelector(index, 'remote', 'fullSync')"></v-text-field>
                                  </div>
                                  <v-btn icon size="small" color="error" class="ml-2"
                                    @click="removePath(index, 'fullSync')">
                                    <v-icon>mdi-delete</v-icon>
                                  </v-btn>
                                </div>
                                <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                  class="mt-2 align-self-start" @click="addPath('fullSync')">
                                  添加路径
                                </v-btn>
                              </div>

                              <v-alert type="info" variant="tonal" density="compact" class="mt-3"
                                icon="mdi-information">
                                <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                                <div class="text-caption">全量扫描配置的网盘目录，并在对应的本地目录生成STRM文件。</div>
                                <div class="text-body-2 mt-2 mb-1"><strong>配置说明：</strong></div>
                                <div class="text-caption">
                                  <div class="mb-1">• <strong>本地STRM目录：</strong>本地STRM文件生成路径</div>
                                  <div>• <strong>网盘媒体库目录：</strong>需要生成本地STRM文件的网盘媒体库路径</div>
                                </div>
                              </v-alert>
                            </v-col>
                          </v-row>
                        </div>

                        <!-- 高级配置 -->
                        <v-expansion-panels variant="tonal" class="mt-6">
                          <v-expansion-panel>
                            <v-expansion-panel-title>
                              <v-icon icon="mdi-tune-variant" class="mr-2"></v-icon>
                              高级配置
                            </v-expansion-panel-title>
                            <v-expansion-panel-text class="pa-4">
                              <v-row>
                                <v-col cols="12" md="3">
                                  <v-switch v-model="config.full_sync_strm_log" label="输出STRM同步日志"
                                    color="primary"></v-switch>
                                </v-col>
                                <v-col cols="12" md="3">
                                  <v-switch v-model="config.full_sync_process_rust" label="Rust模式处理数据"
                                    color="primary"></v-switch>
                                </v-col>
                                <v-col cols="12" md="6">
                                  <v-select v-model="config.full_sync_iter_function" label="迭代函数" :items="[
                                    { title: 'iter_files_with_path_skim', value: 'iter_files_with_path_skim' },
                                    { title: 'iter_files_with_path', value: 'iter_files_with_path' }
                                  ]" chips closable-chips></v-select>
                                </v-col>
                              </v-row>
                              <v-row>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model.number="config.full_sync_batch_num" label="全量同步批处理数量"
                                    type="number" hint="每次批量处理的文件/目录数量" persistent-hint
                                    density="compact"></v-text-field>
                                </v-col>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model.number="config.full_sync_process_num" label="全量同步生成进程数"
                                    type="number" hint="同时执行同步任务的进程数量" persistent-hint density="compact"></v-text-field>
                                </v-col>
                              </v-row>
                              <v-row>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model.number="config.full_sync_remove_unless_max_threshold"
                                    label="清理无效 STRM 最大删除比例阈值 (%)" type="number"
                                    hint="当待删除文件数占本地文件总数的百分比超过此值时，将进入数据稳定性检查（默认 10%）" persistent-hint
                                    density="compact"></v-text-field>
                                </v-col>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model.number="config.full_sync_remove_unless_stable_threshold"
                                    label="清理数据稳定性检查阈值 (%)" type="number" hint="数据稳定性检查的变异系数阈值，低于此值表示删除数据稳定可执行操作（默认 5%）"
                                    persistent-hint density="compact"></v-text-field>
                                </v-col>
                              </v-row>
                            </v-expansion-panel-text>
                          </v-expansion-panel>
                        </v-expansion-panels>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-increment-sync">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.increment_sync_strm_enabled" label="启用"
                              color="warning"></v-switch>
                          </v-col>
                          <v-col cols="12" md="6">
                            <VCronField v-model="config.increment_sync_cron" label="运行增量同步周期" hint="设置增量同步的执行周期"
                              persistent-hint density="compact"></VCronField>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-text-field v-model="incrementSyncMinFileSizeFormatted" label="STRM最小文件大小"
                              hint="小于此值的文件将不生成STRM(单位K,M,G)" persistent-hint density="compact"
                              placeholder="例如: 100M (可为空)" clearable></v-text-field>
                          </v-col>
                        </v-row>
                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.increment_sync_auto_download_mediainfo_enabled" label="下载媒体数据文件"
                              color="warning"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.increment_sync_scrape_metadata_enabled" label="STRM自动刮削"
                              color="primary"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.increment_sync_media_server_refresh_enabled" label="媒体服务器刷新"
                              color="warning"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-select v-model="config.increment_sync_mediaservers" label="媒体服务器" :items="mediaservers"
                              multiple chips closable-chips></v-select>
                          </v-col>
                        </v-row>

                        <v-row v-if="config.increment_sync_scrape_metadata_enabled" class="mt-2 mb-2">
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(item, index) in incrementSyncExcludePaths"
                                :key="`increment-exclude-${index}`" class="mb-2 d-flex align-center">
                                <v-text-field v-model="item.path" label="刮削排除目录" density="compact" variant="outlined"
                                  readonly hide-details class="flex-grow-1 mr-2">
                                </v-text-field>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removeExcludePathEntry(index, 'increment_exclude')" :disabled="!item.path">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-folder-plus-outline" variant="tonal"
                                class="mt-1 align-self-start"
                                @click="openExcludeDirSelector('increment_sync_scrape_metadata_exclude_paths')">
                                添加刮削排除目录
                              </v-btn>
                            </div>
                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-caption">此处添加的本地目录，在STRM文件生成后将不会自动触发刮削。</div>
                            </v-alert>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(pair, index) in incrementSyncPaths" :key="`increment-${index}`"
                                class="mb-2 d-flex align-center">
                                <div class="path-selector flex-grow-1 mr-2">
                                  <v-text-field v-model="pair.local" label="本地STRM目录" density="compact"
                                    append-icon="mdi-folder"
                                    @click:append="openDirSelector(index, 'local', 'incrementSync')"></v-text-field>
                                </div>
                                <v-icon>mdi-pound</v-icon>
                                <div class="path-selector flex-grow-1 ml-2">
                                  <v-text-field v-model="pair.remote" label="网盘媒体库目录" density="compact"
                                    append-icon="mdi-folder-network"
                                    @click:append="openDirSelector(index, 'remote', 'incrementSync')"></v-text-field>
                                </div>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removePath(index, 'incrementSync')">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('incrementSync')">
                                添加路径
                              </v-btn>
                            </div>

                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                              <div class="text-caption">增量扫描配置的网盘目录，并在对应的本地目录生成STRM文件。</div>
                              <div class="text-body-2 mt-2 mb-1"><strong>配置说明：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">• <strong>本地STRM目录：</strong>本地STRM文件生成路径</div>
                                <div>• <strong>网盘媒体库目录：</strong>需要生成本地STRM文件的网盘媒体库路径</div>
                              </div>
                            </v-alert>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(pair, index) in incrementSyncMPPaths" :key="`increment-mp-${index}`"
                                class="mb-2 d-flex align-center">
                                <div class="path-selector flex-grow-1 mr-2">
                                  <v-text-field v-model="pair.local" label="媒体库服务器映射目录"
                                    density="compact"></v-text-field>
                                </div>
                                <v-icon>mdi-pound</v-icon>
                                <div class="path-selector flex-grow-1 ml-2">
                                  <v-text-field v-model="pair.remote" label="MP映射目录" density="compact"></v-text-field>
                                </div>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removePath(index, 'increment-mp')">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('increment-mp')">
                                添加路径
                              </v-btn>
                            </div>

                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>配置说明：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">• 媒体服务器映射路径和MP映射路径不一样时请配置此项，如果不配置则无法正常刷新。</div>
                                <div>• 当映射路径一样时可省略此配置。</div>
                              </div>
                            </v-alert>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-life">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.monitor_life_enabled" label="启用" color="info"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-select v-model="config.monitor_life_event_modes" label="处理事件类型" :items="[
                              { title: '新增事件', value: 'creata' },
                              { title: '删除事件', value: 'remove' },
                              { title: '网盘整理', value: 'transfer' }
                            ]" multiple chips closable-chips></v-select>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.monitor_life_remove_mp_history" label="同步删除历史记录" color="warning"
                              :disabled="config.monitor_life_remove_mp_source"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.monitor_life_remove_mp_source" label="同步删除源文件" color="warning"
                              @change="value => { if (value) config.monitor_life_remove_mp_history = true }"></v-switch>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12" md="4">
                            <v-switch v-model="config.monitor_life_media_server_refresh_enabled" label="媒体服务器刷新"
                              color="warning"></v-switch>
                          </v-col>
                          <v-col cols="12" md="8">
                            <v-select v-model="config.monitor_life_mediaservers" label="媒体服务器" :items="mediaservers"
                              multiple chips closable-chips></v-select>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.monitor_life_auto_download_mediainfo_enabled" label="下载媒体数据文件"
                              color="warning"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.monitor_life_scrape_metadata_enabled" label="STRM自动刮削"
                              color="primary"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-text-field v-model="monitorLifeMinFileSizeFormatted" label="STRM最小文件大小"
                              hint="小于此值的文件将不生成STRM(单位K,M,G)" persistent-hint density="compact"
                              placeholder="例如: 100M (可为空)" clearable></v-text-field>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-text-field v-model.number="config.monitor_life_event_wait_time" label="事件处理延迟时间"
                              type="number" hint="接收到事件后等待的时间，0 则代表不等待 (单位秒)" persistent-hint
                              density="compact"></v-text-field>
                          </v-col>
                        </v-row>

                        <!-- Monitor Life Exclude Paths -->
                        <v-row v-if="config.monitor_life_scrape_metadata_enabled" class="mt-2 mb-2">
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(item, index) in monitorLifeExcludePaths" :key="`life-exclude-${index}`"
                                class="mb-2 d-flex align-center">
                                <v-text-field v-model="item.path" label="刮削排除目录" density="compact" variant="outlined"
                                  readonly hide-details class="flex-grow-1 mr-2">
                                </v-text-field>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removeExcludePathEntry(index, 'life_exclude')" :disabled="!item.path">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-folder-plus-outline" variant="tonal"
                                class="mt-1 align-self-start"
                                @click="openExcludeDirSelector('monitor_life_scrape_metadata_exclude_paths')">
                                添加刮削排除目录
                              </v-btn>
                            </div>
                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-caption">此处添加的本地目录，在115生活事件监控生成STRM后将不会自动触发刮削。</div>
                            </v-alert>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(pair, index) in monitorLifePaths" :key="`life-${index}`"
                                class="mb-2 d-flex align-center">
                                <div class="path-selector flex-grow-1 mr-2">
                                  <v-text-field v-model="pair.local" label="本地STRM目录" density="compact"
                                    append-icon="mdi-folder"
                                    @click:append="openDirSelector(index, 'local', 'monitorLife')"></v-text-field>
                                </div>
                                <v-icon>mdi-pound</v-icon>
                                <div class="path-selector flex-grow-1 ml-2">
                                  <v-text-field v-model="pair.remote" label="网盘媒体库目录" density="compact"
                                    append-icon="mdi-folder-network"
                                    @click:append="openDirSelector(index, 'remote', 'monitorLife')"></v-text-field>
                                </div>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removePath(index, 'monitorLife')">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('monitorLife')">
                                添加路径
                              </v-btn>
                            </div>

                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                              <div class="text-caption">监控115生活（上传、移动、接收文件、删除、复制）事件，自动在本地对应目录生成STRM文件或者删除STRM文件。</div>
                              <div class="text-body-2 mt-2 mb-1"><strong>配置说明：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">• <strong>本地STRM目录：</strong>本地STRM文件生成路径</div>
                                <div>• <strong>网盘媒体库目录：</strong>需要生成本地STRM文件的网盘媒体库路径</div>
                              </div>
                            </v-alert>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(pair, index) in monitorLifeMpPaths" :key="`life-mp-${index}`"
                                class="mb-2 d-flex align-center">
                                <div class="path-selector flex-grow-1 mr-2">
                                  <v-text-field v-model="pair.local" label="媒体库服务器映射目录"
                                    density="compact"></v-text-field>
                                </div>
                                <v-icon>mdi-pound</v-icon>
                                <div class="path-selector flex-grow-1 ml-2">
                                  <v-text-field v-model="pair.remote" label="MP映射目录" density="compact"></v-text-field>
                                </div>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removePath(index, 'monitorLifeMp')">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('monitorLifeMp')">
                                添加路径
                              </v-btn>
                            </div>

                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>配置说明：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">• 媒体服务器映射路径和MP映射路径不一样时请配置此项，如果不配置则无法正常刷新。</div>
                                <div>• 当映射路径一样时可省略此配置。</div>
                              </div>
                            </v-alert>
                          </v-col>
                        </v-row>

                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-caption">注意：当 MoviePilot 主程序运行整理任务时 115生活事件 监控会自动暂停，整理运行完成后会继续监控。</div>
                        </v-alert>

                        <v-row class="mt-4">
                          <v-col cols="12">
                            <v-btn color="info" variant="outlined" prepend-icon="mdi-bug-check"
                              @click="checkLifeEventStatus">
                              故障检查
                            </v-btn>
                            <div class="text-caption text-grey mt-2">
                              检查115生活事件进程状态，测试数据拉取功能，并提供详细的调试信息
                            </div>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-api-strm">
                      <v-card-text>
                        <v-alert type="info" variant="tonal" density="compact" class="mb-4" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                          <div class="text-caption mb-2">API STRM 生成功能允许第三方开发者通过 HTTP API 调用，批量生成 STRM 文件。</div>
                          <div class="text-caption">
                            详细 API 文档请参考：
                            <a href="https://github.com/DDSRem-Dev/MoviePilot-Plugins/blob/main/docs/p115strmhelper/API_STRM生成功能文档.md"
                              target="_blank" rel="noopener noreferrer"
                              style="color: inherit; text-decoration: underline;">
                              GitHub 文档链接
                            </a>
                          </div>
                        </v-alert>

                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.api_strm_scrape_metadata_enabled" label="STRM自动刮削" color="primary"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.api_strm_media_server_refresh_enabled" label="媒体服务器刷新"
                              color="warning" density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="6">
                            <v-select v-model="config.api_strm_mediaservers" label="媒体服务器" :items="mediaservers"
                              multiple chips closable-chips density="compact"></v-select>
                          </v-col>
                        </v-row>

                        <v-divider class="my-4"></v-divider>

                        <div class="text-subtitle-2 mb-2">路径映射配置:</div>
                        <v-alert type="info" variant="tonal" density="compact" class="mb-3" icon="mdi-information">
                          <div class="text-caption">配置网盘路径到本地路径的映射关系。当 API 请求中未指定 local_path 时，系统会根据此配置自动匹配路径。</div>
                        </v-alert>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(pair, index) in apiStrmPaths" :key="`api-strm-${index}`"
                                class="mb-2 d-flex align-center">
                                <div class="path-selector flex-grow-1 mr-2">
                                  <v-text-field v-model="pair.local" label="本地STRM目录" density="compact"
                                    append-icon="mdi-folder"
                                    @click:append="openDirSelector(index, 'local', 'apiStrm')"></v-text-field>
                                </div>
                                <v-icon>mdi-pound</v-icon>
                                <div class="path-selector flex-grow-1 ml-2">
                                  <v-text-field v-model="pair.remote" label="网盘媒体库目录" density="compact"
                                    append-icon="mdi-folder-network"
                                    @click:append="openDirSelector(index, 'remote', 'apiStrm')"></v-text-field>
                                </div>
                                <v-btn icon size="small" color="error" class="ml-2"
                                  @click="removePath(index, 'apiStrm')">
                                  <v-icon>mdi-delete</v-icon>
                                </v-btn>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('apiStrm')">
                                添加路径
                              </v-btn>
                            </div>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                  </v-window>
                </v-card-text>
              </v-window-item>

              <!-- 网盘管理分类 -->
              <v-window-item value="category-pan">
                <v-card-text class="pa-0">
                  <v-tabs v-model="panSubTab" color="primary" class="sub-category-tabs" slider-color="primary">
                    <v-tab value="tab-pan-transfer" class="sub-tab">
                      <v-icon size="small" start>mdi-transfer</v-icon>网盘整理
                    </v-tab>
                    <v-tab value="tab-pan-mount" class="sub-tab">
                      <v-icon size="small" start>mdi-folder-network</v-icon>网盘挂载
                    </v-tab>
                    <v-tab value="tab-directory-upload" class="sub-tab">
                      <v-icon size="small" start>mdi-upload</v-icon>目录上传
                    </v-tab>
                  </v-tabs>
                  <v-divider></v-divider>
                  <v-window v-model="panSubTab" :touch="false">
                    <v-window-item value="tab-pan-transfer">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="4">
                            <v-switch v-model="config.pan_transfer_enabled" label="启用" color="info"></v-switch>
                          </v-col>
                        </v-row>

                        <!-- 待整理和未识别目录 -->
                        <v-card variant="outlined" class="mt-4">
                          <v-card-title class="text-subtitle-1">
                            <v-icon start>mdi-folder-move</v-icon>
                            待整理和未识别目录
                          </v-card-title>
                          <v-card-text>
                            <v-row>
                              <v-col cols="12">
                                <div class="d-flex flex-column">
                                  <div v-for="(path, index) in panTransferPaths" :key="`pan-${index}`"
                                    class="mb-2 d-flex align-center">
                                    <v-text-field v-model="path.path" label="网盘待整理目录" density="compact"
                                      append-icon="mdi-folder-network"
                                      @click:append="openDirSelector(index, 'remote', 'panTransfer')"
                                      class="flex-grow-1"></v-text-field>
                                    <v-btn icon size="small" color="primary" class="ml-2" @click="manualTransfer(index)"
                                      :disabled="!path.path" title="手动整理此目录">
                                      <v-icon>mdi-play</v-icon>
                                    </v-btn>
                                    <v-btn icon size="small" color="error" class="ml-2"
                                      @click="removePanTransferPath(index)">
                                      <v-icon>mdi-delete</v-icon>
                                    </v-btn>
                                  </div>
                                  <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                    class="mt-2 align-self-start" @click="addPanTransferPath">
                                    添加路径
                                  </v-btn>
                                </div>
                              </v-col>
                            </v-row>

                            <v-row class="mt-4">
                              <v-col cols="12">
                                <v-text-field v-model="config.pan_transfer_unrecognized_path" label="网盘整理未识别目录"
                                  density="compact" append-icon="mdi-folder-network"
                                  @click:append="openDirSelector('unrecognized', 'remote', 'panTransferUnrecognized')"></v-text-field>
                                <v-alert type="info" variant="tonal" density="compact" class="mt-3"
                                  icon="mdi-information">
                                  <div class="text-caption">提示：此目录用于存放整理过程中未能识别的媒体文件。</div>
                                </v-alert>
                                <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                                  <div class="text-caption">注意：未识别目录不能设置在任何媒体库目录或待整理目录的内部。</div>
                                </v-alert>
                              </v-col>
                            </v-row>
                          </v-card-text>
                        </v-card>

                        <!-- 分享转存目录 -->
                        <v-card variant="outlined" class="mt-4">
                          <v-card-title class="text-subtitle-1">
                            <v-icon start>mdi-share-variant</v-icon>
                            分享转存目录
                          </v-card-title>
                          <v-card-text>
                            <v-row>
                              <v-col cols="12">
                                <div class="d-flex flex-column">
                                  <div v-for="(path, index) in shareReceivePaths" :key="`share-${index}`"
                                    class="mb-2 d-flex align-center">
                                    <v-text-field v-model="path.path" label="分享转存目录" density="compact"
                                      append-icon="mdi-folder-network"
                                      @click:append="openDirSelector(index, 'remote', 'shareReceive')"
                                      class="flex-grow-1"></v-text-field>
                                    <v-btn icon size="small" color="error" class="ml-2"
                                      @click="removeShareReceivePath(index)">
                                      <v-icon>mdi-delete</v-icon>
                                    </v-btn>
                                  </div>
                                  <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                    class="mt-2 align-self-start" @click="addShareReceivePath">
                                    添加路径
                                  </v-btn>
                                </div>
                              </v-col>
                            </v-row>
                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-caption">提示：此目录用于存放通过分享链接转存的资源。</div>
                            </v-alert>
                          </v-card-text>
                        </v-card>

                        <!-- 离线下载目录 -->
                        <v-card variant="outlined" class="mt-4">
                          <v-card-title class="text-subtitle-1">
                            <v-icon start>mdi-download</v-icon>
                            离线下载目录
                          </v-card-title>
                          <v-card-text>
                            <v-row>
                              <v-col cols="12">
                                <div class="d-flex flex-column">
                                  <div v-for="(path, index) in offlineDownloadPaths" :key="`offline-${index}`"
                                    class="mb-2 d-flex align-center">
                                    <v-text-field v-model="path.path" label="离线下载目录" density="compact"
                                      append-icon="mdi-folder-network"
                                      @click:append="openDirSelector(index, 'remote', 'offlineDownload')"
                                      class="flex-grow-1"></v-text-field>
                                    <v-btn icon size="small" color="error" class="ml-2"
                                      @click="removeOfflineDownloadPath(index)">
                                      <v-icon>mdi-delete</v-icon>
                                    </v-btn>
                                  </div>
                                  <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                    class="mt-2 align-self-start" @click="addOfflineDownloadPath">
                                    添加路径
                                  </v-btn>
                                </div>
                              </v-col>
                            </v-row>
                            <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                              <div class="text-caption">提示：此目录用于存放通过离线下载功能下载的资源。</div>
                            </v-alert>
                          </v-card-text>
                        </v-card>

                        <v-divider class="my-4"></v-divider>

                        <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>配置说明：</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">使用本功能需要先进入 设定-目录 进行配置：</div>
                            <div class="mb-1">1. 添加目录配置卡，按需配置媒体类型和媒体类别，资源存储选择115网盘，资源目录输入网盘待整理文件夹</div>
                            <div class="mb-1">2. 自动整理模式选择手动整理，媒体库存储依旧选择115网盘，并配置好媒体库路径，整理方式选择移动，按需配置分类、重命名、通知</div>
                            <div>3. 配置完成目录设置后只需要在上方 网盘待整理目录 填入 网盘待整理文件夹 即可</div>
                          </div>
                        </v-alert>

                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-caption">注意：配置目录时不能选择刮削元数据，否则可能导致风控！</div>
                        </v-alert>

                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-body-2 mb-1"><strong>注意事项：</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• 阿里云盘，115网盘分享链接秒传或转存都依赖于网盘整理</div>
                            <div class="mb-1">• TG/Slack资源搜索转存也依赖于网盘整理</div>
                            <div>• 当阿里云盘分享秒传未能识别分享媒体信息时，会自动将资源转存到网盘整理未识别目录，后续需要用户手动重命名整理</div>
                          </div>
                        </v-alert>

                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-caption">注意：115生活事件监控默认会忽略网盘整理触发的移动事件，所以推荐使用MP整理事件监控生成STRM</div>
                        </v-alert>

                        <v-row class="mt-4">
                          <v-col cols="12">
                            <v-btn color="info" variant="outlined" prepend-icon="mdi-bug-check"
                              @click="checkLifeEventStatus">
                              故障检查
                            </v-btn>
                            <div class="text-caption text-grey mt-2">
                              检查115生活事件进程状态，测试数据拉取功能，并提供详细的调试信息
                            </div>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-pan-mount">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="6">
                            <v-switch v-model="config.fuse_enabled" label="启用" color="success" density="compact"
                              hint="将115网盘挂载为本地文件系统" persistent-hint></v-switch>
                          </v-col>
                          <v-col cols="12" md="6">
                            <v-text-field v-model="config.fuse_mountpoint" label="挂载点路径" hint="文件系统挂载的本地路径"
                              persistent-hint density="compact" variant="outlined" hide-details="auto"
                              placeholder="/mnt/115"></v-text-field>
                          </v-col>
                        </v-row>
                        <v-row>
                          <v-col cols="12" md="4">
                            <v-text-field v-model.number="config.fuse_readdir_ttl" label="目录读取缓存 TTL（秒）" type="number"
                              hint="目录列表缓存时间，默认60秒" persistent-hint density="compact" variant="outlined"
                              hide-details="auto" min="0"></v-text-field>
                          </v-col>
                          <v-col cols="12" md="4">
                            <v-text-field v-model.number="config.fuse_uid" label="文件所有者 UID" type="number"
                              hint="挂载文件的用户ID，留空则使用当前运行用户" persistent-hint density="compact" variant="outlined"
                              hide-details="auto" min="0" clearable></v-text-field>
                          </v-col>
                          <v-col cols="12" md="4">
                            <v-text-field v-model.number="config.fuse_gid" label="文件所有者 GID" type="number"
                              hint="挂载文件的组ID，留空则使用当前运行用户" persistent-hint density="compact" variant="outlined"
                              hide-details="auto" min="0" clearable></v-text-field>
                          </v-col>
                        </v-row>

                        <!-- STRM 文件生成内容接管 -->
                        <v-divider class="my-4"></v-divider>
                        <v-row>
                          <v-col cols="12">
                            <v-switch v-model="config.fuse_strm_takeover_enabled" label="接管 STRM 文件生成内容" color="primary"
                              density="compact" hint="启用后，匹配规则的文件将生成指向挂载路径的 STRM 内容" persistent-hint></v-switch>
                          </v-col>
                        </v-row>
                        <v-expand-transition>
                          <div v-if="config.fuse_strm_takeover_enabled">
                            <v-divider class="my-4"></v-divider>
                            <v-alert type="info" variant="tonal" density="compact" class="mb-4" icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>STRM URL 生成优先级：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">1. <strong>URL 自定义模板</strong>（如果启用）：优先使用 Jinja2 模板渲染</div>
                                <div class="mb-1">2. <strong>FUSE STRM 接管</strong>（如果启用且匹配规则）：生成指向挂载路径的 STRM 内容</div>
                                <div>3. <strong>默认格式</strong>：使用基础设置中的「STRM文件URL格式」和「STRM URL 文件名称编码」</div>
                              </div>
                            </v-alert>
                            <v-divider class="mb-4"></v-divider>
                            <v-row>
                              <v-col cols="12" md="6">
                                <v-text-field v-model="config.fuse_strm_mount_dir" label="媒体服务器网盘挂载目录"
                                  hint="媒体服务器中配置的 115 网盘挂载路径" persistent-hint density="compact" variant="outlined"
                                  hide-details="auto" placeholder="/media/115"></v-text-field>
                              </v-col>
                            </v-row>
                            <v-row>
                              <v-col cols="12">
                                <v-btn color="primary" variant="outlined" prepend-icon="mdi-code-tags" size="small"
                                  @click="openConfigGeneratorDialog">
                                  生成 emby2Alist 配置
                                </v-btn>
                              </v-col>
                            </v-row>
                            <v-row>
                              <v-col cols="12">
                                <div class="text-body-2 mb-2"><strong>接管规则：</strong></div>
                                <div class="d-flex flex-column">
                                  <v-card v-for="(rule, index) in fuseStrmTakeoverRules"
                                    :key="`fuse-strm-takeover-${index}`" variant="outlined" class="mb-3">
                                    <v-card-text>
                                      <div class="d-flex align-center mb-2">
                                        <span class="text-caption text-medium-emphasis">规则 #{{ index + 1 }}</span>
                                        <v-spacer></v-spacer>
                                        <v-btn icon size="small" color="error"
                                          @click="removePath(index, 'fuseStrmTakeover')">
                                          <v-icon>mdi-delete</v-icon>
                                        </v-btn>
                                      </div>

                                      <!-- 匹配方式选择 -->
                                      <div class="mb-3">
                                        <div class="text-caption text-medium-emphasis mb-2">选择匹配方式（可多选）：</div>
                                        <div class="d-flex flex-wrap gap-3">
                                          <v-switch v-model="rule._use_extensions" label="文件后缀" density="compact"
                                            color="primary" hide-details class="ma-0"></v-switch>
                                          <v-switch v-model="rule._use_names" label="文件名称" density="compact"
                                            color="primary" hide-details class="ma-0"></v-switch>
                                          <v-switch v-model="rule._use_paths" label="网盘路径" density="compact"
                                            color="primary" hide-details class="ma-0"></v-switch>
                                        </div>
                                      </div>

                                      <!-- 文件后缀 -->
                                      <v-expand-transition>
                                        <div v-if="rule._use_extensions">
                                          <v-textarea v-model="rule.extensions" label="文件后缀（每行一个，例如：mkv、mp4）"
                                            hint="匹配的文件后缀，不包含点号，每行一个" persistent-hint density="compact"
                                            variant="outlined" rows="2" class="mb-2"
                                            placeholder="mkv&#10;mp4&#10;avi"></v-textarea>
                                        </div>
                                      </v-expand-transition>

                                      <!-- 文件名称白名单 -->
                                      <v-expand-transition>
                                        <div v-if="rule._use_names">
                                          <v-textarea v-model="rule.names" label="文件名称白名单（每行一个，支持部分匹配）"
                                            hint="文件名包含这些关键词时匹配，每行一个" persistent-hint density="compact"
                                            variant="outlined" rows="2" class="mb-2"
                                            placeholder="蓝光&#10;BluRay"></v-textarea>
                                        </div>
                                      </v-expand-transition>

                                      <!-- 网盘文件夹路径 -->
                                      <v-expand-transition>
                                        <div v-if="rule._use_paths">
                                          <v-textarea v-model="rule.paths" label="网盘文件夹路径（每行一个，支持部分匹配）"
                                            hint="文件路径包含这些路径时匹配，每行一个" persistent-hint density="compact"
                                            variant="outlined" rows="2" placeholder="/电影/4K&#10;/电视剧"></v-textarea>
                                        </div>
                                      </v-expand-transition>
                                    </v-card-text>
                                  </v-card>
                                  <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                    class="align-self-start" @click="addPath('fuseStrmTakeover')">
                                    添加接管规则
                                  </v-btn>
                                </div>
                                <v-alert type="info" variant="tonal" density="compact" class="mt-3"
                                  icon="mdi-information">
                                  <div class="text-caption">
                                    <div class="mb-1">• 三种匹配方式可以组合使用，<strong>同时满足</strong>（与关系）才会匹配</div>
                                    <div class="mb-1">• 如果某个匹配条件为空，则<strong>不检查</strong>该条件</div>
                                    <div class="mb-1">• <strong>匹配成功后生成的 STRM 内容：</strong></div>
                                    <div class="mb-1">
                                      格式：<code>{{ config.fuse_strm_mount_dir || '媒体服务器挂载目录' }}/文件网盘路径</code></div>
                                    <div> 示例：如果挂载目录为 <code>/media/115</code>，文件网盘路径为 <code>/电影/示例.mkv</code>，则生成的 STRM
                                      内容为
                                      <code>/media/115/电影/示例.mkv</code>
                                    </div>
                                  </div>
                                </v-alert>
                              </v-col>
                            </v-row>
                          </div>
                        </v-expand-transition>

                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-body-2 mb-1"><strong>平台限制说明：</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• <strong>Windows 系统不支持：</strong>FUSE 功能基于 Linux 文件系统，无法在 Windows 环境下运行
                            </div>
                            <div class="mb-1">• <strong>Linux 裸机：</strong>理论上支持，需要安装 libfuse（libfuse2 或 libfuse3）</div>
                            <div class="mb-1">• <strong>macOS 裸机：</strong>理论上支持，需要安装 macFUSE</div>
                            <div>• <strong>推荐使用 Docker 容器：</strong>目前仅对 Docker 容器环境有较好的支持和测试，建议在 Docker 容器中使用此功能</div>
                          </div>
                        </v-alert>
                        <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                          <div class="text-caption mb-2">启用后，115网盘将挂载为容器内的文件系统，可通过文件管理器直接访问。配合上方的"STRM
                            文件生成内容接管"功能，可以让生成的 STRM
                            文件直接指向挂载路径，实现本地文件系统访问。</div>
                          <div class="text-body-2 mt-2 mb-1"><strong>配置说明：</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• <strong>挂载点路径：</strong>容器内的挂载路径，必须是已存在的目录（例如：<code>/media/115</code> 或
                              <code>/data/115</code>）
                            </div>
                            <div class="mb-1">• <strong>目录读取缓存 TTL：</strong>目录列表缓存时间，默认60秒</div>
                            <div class="mb-1">• <strong>文件所有者 UID/GID：</strong>设置挂载文件的用户和组ID，留空则自动使用当前运行用户（Docker
                              容器中建议设置为非
                              root 用户）
                            </div>
                            <div class="mb-1">• <strong>容器权限：</strong>需要容器以 <code>--privileged</code> 或
                              <code>--cap-add SYS_ADMIN</code>
                              权限运行
                            </div>
                            <div>• <strong>STRM 接管：</strong>启用上方的"接管 STRM 文件生成内容"后，匹配规则的文件将生成指向挂载路径的 STRM
                              内容，媒体服务器可直接通过挂载路径访问文件</div>
                          </div>
                        </v-alert>
                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-body-2 mb-1"><strong>重要提示：</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• <strong>切勿直接使用媒体服务器刮削挂载路径</strong>，这会导致网盘风控</div>
                            <div>• <strong>正确方法：</strong>使用本插件的 STRM 文件生成功能，在本地生成 STRM 文件后，再让媒体服务器对 STRM 文件进行刮削</div>
                          </div>
                        </v-alert>
                        <v-alert type="info" variant="tonal" density="compact" class="mt-3"
                          icon="mdi-book-open-page-variant">
                          <div class="text-body-2 mb-1"><strong>配置教程：</strong></div>
                          <div class="text-caption">
                            详细的 FUSE 挂载配置指南请参考：
                            <a href="https://blog.ddsrem.com/archives/115strmhelper-fuse-use" target="_blank"
                              rel="noopener noreferrer" style="color: inherit; text-decoration: underline;">FUSE
                              挂载详细配置指南</a>
                          </div>
                        </v-alert>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-directory-upload">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="4">
                            <v-switch v-model="config.directory_upload_enabled" label="启用" color="info"
                              density="compact" hide-details></v-switch>
                          </v-col>
                          <v-col cols="12" md="8">
                            <v-select v-model="config.directory_upload_mode" label="监控模式" :items="[
                              { title: '兼容模式', value: 'compatibility' },
                              { title: '性能模式', value: 'fast' }
                            ]" chips closable-chips density="compact" hide-details></v-select>
                          </v-col>
                        </v-row>
                        <v-row>
                          <v-col cols="12" md="6">
                            <v-text-field v-model="config.directory_upload_uploadext" label="上传文件扩展名"
                              hint="指定哪些扩展名的文件会被上传到115网盘，多个用逗号分隔" persistent-hint density="compact" variant="outlined"
                              hide-details="auto"></v-text-field>
                          </v-col>
                          <v-col cols="12" md="6">
                            <v-text-field v-model="config.directory_upload_copyext" label="复制文件扩展名"
                              hint="指定哪些扩展名的文件会被复制到本地目标目录，多个用逗号分隔" persistent-hint density="compact" variant="outlined"
                              hide-details="auto"></v-text-field>
                          </v-col>
                        </v-row>

                        <v-divider class="my-3"></v-divider>
                        <div class="text-subtitle-2 mb-2">路径配置:</div>

                        <div v-for="(pair, index) in directoryUploadPaths" :key="`upload-${index}`"
                          class="path-group mb-3 pa-2 border rounded">
                          <v-row dense>
                            <!-- 本地监控目录 -->
                            <v-col cols="12" md="6">
                              <v-text-field v-model="pair.src" label="本地监控目录" density="compact" variant="outlined"
                                hide-details append-icon="mdi-folder-search-outline"
                                @click:append="openDirSelector(index, 'local', 'directoryUpload', 'src')">
                                <template v-slot:prepend-inner>
                                  <v-icon color="blue">mdi-folder-table</v-icon>
                                </template>
                              </v-text-field>
                            </v-col>
                            <!-- 网盘上传目录 -->
                            <v-col cols="12" md="6">
                              <v-text-field v-model="pair.dest_remote" label="网盘上传目标目录" density="compact"
                                variant="outlined" hide-details append-icon="mdi-folder-network-outline"
                                @click:append="openDirSelector(index, 'remote', 'directoryUpload', 'dest_remote')">
                                <template v-slot:prepend-inner>
                                  <v-icon color="green">mdi-cloud-upload</v-icon>
                                </template>
                              </v-text-field>
                            </v-col>
                          </v-row>
                          <v-row dense class="mt-1">
                            <!-- 非上传文件目标目录 -->
                            <v-col cols="12" md="6">
                              <v-text-field v-model="pair.dest_local" label="本地复制目标目录 (可选)" density="compact"
                                variant="outlined" hide-details append-icon="mdi-folder-plus-outline"
                                @click:append="openDirSelector(index, 'local', 'directoryUpload', 'dest_local')">
                                <template v-slot:prepend-inner>
                                  <v-icon color="orange">mdi-content-copy</v-icon>
                                </template>
                              </v-text-field>
                            </v-col>
                            <!-- STRM 输出目录 -->
                            <v-col cols="12" md="6">
                              <v-text-field v-model="pair.dest_strm" label="STRM 输出目录 (可选)" density="compact"
                                variant="outlined" append-icon="mdi-file-document-outline" hide-details="auto"
                                @click:append="openDirSelector(index, 'local', 'directoryUpload', 'dest_strm')">
                                <template v-slot:prepend-inner>
                                  <v-icon color="purple">mdi-file-star</v-icon>
                                </template>
                              </v-text-field>
                            </v-col>
                          </v-row>
                          <v-row dense class="mt-1">
                            <!-- 删除源文件开关 -->
                            <v-col cols="12" md="10" class="d-flex align-center">
                              <v-switch v-model="pair.delete" label="处理后删除源文件" color="error" density="compact"
                                hide-details></v-switch>
                            </v-col>
                            <!-- 删除按钮 -->
                            <v-col cols="12" md="2" class="d-flex align-center justify-end">
                              <v-btn icon="mdi-delete-outline" size="small" color="error" variant="text" title="删除此路径配置"
                                @click="removePath(index, 'directoryUpload')">
                              </v-btn>
                            </v-col>
                          </v-row>
                        </div>

                        <v-btn size="small" prepend-icon="mdi-plus-box-multiple-outline" variant="tonal" class="mt-2"
                          color="primary" @click="addPath('directoryUpload')">
                          添加监控路径组
                        </v-btn>

                        <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• 监控指定的"本地监控目录"</div>
                            <div class="mb-1">• 当目录中出现新文件时：</div>
                            <div class="ml-4 mb-1">- 如果文件扩展名匹配"上传文件扩展名"，则将其上传到对应的"网盘上传目标目录"；若配置了"STRM
                              输出目录"，上传成功后会立即在该目录生成对应
                              .strm 文件
                            </div>
                            <div class="ml-4 mb-1">- 如果文件扩展名匹配"复制文件扩展名"，则将其复制到对应的"本地复制目标目录"</div>
                            <div class="mb-1">• 处理完成后，如果"删除源文件"开关打开，则会删除原始文件</div>
                            <div class="mb-1">• 扩展名不匹配的文件将被忽略</div>
                            <div class="mb-1">• "STRM 输出目录"：上传成功后立即在此目录生成对应 .strm 文件，目录结构同监控目录相对路径</div>
                          </div>
                          <strong>注意:</strong><br>
                          - 请确保MoviePilot对本地目录有读写权限，对网盘目录有写入权限。<br>
                          - "本地复制目标目录"和"STRM 输出目录"是可选的，如果不填，则仅执行上传操作（如果匹配）。<br>
                          - 监控模式："性能模式"使用系统原生文件系统事件，适用于物理路径，性能高且更稳定；"兼容模式"使用轮询方式，适用于网络共享目录（如SMB/NFS），性能较低但兼容性好。
                        </v-alert>
                      </v-card-text>
                    </v-window-item>
                  </v-window>
                </v-card-text>
              </v-window-item>

              <!-- 其他功能分类 -->
              <v-window-item value="category-other">
                <v-card-text class="pa-0">
                  <v-tabs v-model="otherSubTab" color="primary" class="sub-category-tabs" slider-color="primary">
                    <v-tab value="tab-sync-del" class="sub-tab">
                      <v-icon size="small" start>mdi-delete-sweep</v-icon>同步删除
                    </v-tab>
                    <v-tab value="tab-tg-search" class="sub-tab">
                      <v-icon size="small" start>mdi-tab-search</v-icon>频道搜索
                    </v-tab>
                    <v-tab value="tab-cleanup" class="sub-tab">
                      <v-icon size="small" start>mdi-broom</v-icon>定期清理
                    </v-tab>
                    <v-tab value="tab-same-playback" class="sub-tab">
                      <v-icon size="small" start>mdi:code-block-parentheses</v-icon>多端播放
                    </v-tab>
                  </v-tabs>
                  <v-divider></v-divider>
                  <v-window v-model="otherSubTab" :touch="false">
                    <v-window-item value="tab-sync-del">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.sync_del_enabled" label="启用同步删除" color="warning"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.sync_del_notify" label="发送通知" color="success"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.sync_del_source" label="删除源文件" color="error"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.sync_del_p115_force_delete_files" label="强制删除文件" color="warning"
                              density="compact"></v-switch>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12" md="6">
                            <v-switch v-model="config.sync_del_remove_versions" label="开启多版本删除" color="info"
                              density="compact" chips closable-chips hint="请查看下方警告提示了解详细说明" persistent-hint></v-switch>
                          </v-col>
                          <v-col cols="12" md="6">
                            <v-select v-model="config.sync_del_mediaservers" label="媒体服务器" :items="embyMediaservers"
                              multiple density="compact" chips closable-chips hint="用于获取TMDB ID，仅支持Emby"
                              persistent-hint></v-select>
                          </v-col>
                        </v-row>

                        <v-row>
                          <v-col cols="12">
                            <div class="d-flex flex-column">
                              <div v-for="(path, index) in syncDelLibraryPaths" :key="`sync-del-${index}`"
                                class="mb-3 pa-3 border rounded">
                                <v-row dense>
                                  <v-col cols="12" md="4">
                                    <v-text-field v-model="path.mediaserver" label="媒体服务器STRM路径" density="compact"
                                      variant="outlined" hint="例如：/media/strm" persistent-hint></v-text-field>
                                  </v-col>
                                  <v-col cols="12" md="4">
                                    <v-text-field v-model="path.moviepilot" label="MoviePilot路径" density="compact"
                                      variant="outlined" hint="例如：/mnt/strm" persistent-hint
                                      append-icon="mdi-folder-home"
                                      @click:append="openDirSelector(index, 'local', 'syncDelLibrary', 'moviepilot')"></v-text-field>
                                  </v-col>
                                  <v-col cols="12" md="4">
                                    <v-text-field v-model="path.p115" label="115网盘媒体库路径" density="compact"
                                      variant="outlined" hint="例如：/影视" persistent-hint append-icon="mdi-cloud"
                                      @click:append="openDirSelector(index, 'remote', 'syncDelLibrary', 'p115')"></v-text-field>
                                  </v-col>
                                </v-row>
                                <v-row dense>
                                  <v-col cols="12" class="d-flex justify-end">
                                    <v-btn icon size="small" color="error" @click="removePath(index, 'syncDelLibrary')">
                                      <v-icon>mdi-delete</v-icon>
                                    </v-btn>
                                  </v-col>
                                </v-row>
                              </div>
                              <v-btn size="small" prepend-icon="mdi-plus" variant="outlined"
                                class="mt-2 align-self-start" @click="addPath('syncDelLibrary')">
                                添加路径映射
                              </v-btn>
                            </div>
                          </v-col>
                        </v-row>

                        <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>关于路径映射：</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• <strong>媒体服务器STRM路径：</strong>媒体服务器中STRM文件的实际路径</div>
                            <div class="mb-1">• <strong>MoviePilot路径：</strong>MoviePilot中对应的路径</div>
                            <div>• <strong>115网盘媒体库路径：</strong>115网盘中媒体库的路径</div>
                          </div>
                        </v-alert>

                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-caption">
                            <div class="mb-1">• 不正确配置会导致查询不到转移记录！</div>
                            <div class="mb-1">• 需要使用神医助手PRO且版本在v3.0.0.3及以上或神医助手社区版且版本在v2.0.0.27及以上！</div>
                            <div class="mb-1">• 同步删除多版本功能需要使用助手Pro v3.0.0.22才支持！</div>
                            <div>•
                              <strong>开启多版本删除：</strong>开启后会将电影和电视剧季删除通过神医返回的路径改为电影单部/电视剧单集删除，从而防止误删其它版本，如果无多版本电影和电视剧季删除的需求，推荐关闭此按钮，提升删除效率
                            </div>
                          </div>
                        </v-alert>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-tg-search">
                      <v-card-text>
                        <!-- Nullbr 配置 -->
                        <v-card variant="outlined" class="mb-6">
                          <v-card-item>
                            <v-card-title class="d-flex align-center">
                              <v-icon start>mdi-cog-outline</v-icon>
                              <span class="text-h6">Nullbr 搜索配置</span>
                            </v-card-title>
                          </v-card-item>
                          <v-card-text>
                            <v-row>
                              <v-col cols="12" md="6">
                                <v-text-field v-model="config.nullbr_app_id" label="Nullbr APP ID" hint="从 Nullbr 官网申请"
                                  persistent-hint density="compact" variant="outlined"></v-text-field>
                              </v-col>
                              <v-col cols="12" md="6">
                                <v-text-field v-model="config.nullbr_api_key" label="Nullbr API KEY"
                                  hint="从 Nullbr 官网申请" persistent-hint density="compact"
                                  variant="outlined"></v-text-field>
                              </v-col>
                            </v-row>
                          </v-card-text>
                        </v-card>

                        <!-- 自定义频道搜索配置 -->
                        <v-card variant="outlined">
                          <v-card-item>
                            <v-card-title class="d-flex align-center">
                              <v-icon start>mdi-telegram</v-icon>
                              <span class="text-h6">自定义Telegram频道</span>
                            </v-card-title>
                          </v-card-item>
                          <v-card-text>
                            <div v-for="(channel, index) in tgChannels" :key="index" class="d-flex align-center mb-4">
                              <v-text-field v-model="channel.name" label="频道名称" placeholder="例如：爱影115资源分享频道"
                                density="compact" variant="outlined" hide-details class="mr-3"></v-text-field>
                              <v-text-field v-model="channel.id" label="频道ID" placeholder="例如：ayzgzf" density="compact"
                                variant="outlined" hide-details class="mr-3"></v-text-field>
                              <v-btn icon size="small" color="error" variant="tonal" @click="removeTgChannel(index)"
                                title="删除此频道">
                                <v-icon>mdi-delete-outline</v-icon>
                              </v-btn>
                            </div>

                            <!-- 操作按钮组 -->
                            <div class="d-flex ga-2">
                              <v-btn size="small" prepend-icon="mdi-plus-circle-outline" variant="tonal" color="primary"
                                @click="addTgChannel">
                                添加频道
                              </v-btn>
                              <v-btn size="small" prepend-icon="mdi-import" variant="tonal" @click="openImportDialog">
                                一键导入
                              </v-btn>
                            </div>
                          </v-card-text>
                        </v-card>

                        <v-alert type="info" variant="tonal" density="compact" class="mt-6" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>Telegram频道搜索功能说明</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• 您可以同时配置 Nullbr 和下方的自定义频道列表</div>
                            <div>• 系统会整合两者的搜索结果，为您提供更广泛的资源范围</div>
                          </div>
                        </v-alert>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-cleanup">
                      <v-card-text>
                        <v-alert type="warning" variant="tonal" density="compact" class="mb-4" icon="mdi-alert">
                          <div class="text-caption">注意，清空 回收站/最近接收 后文件不可恢复，如果产生重要数据丢失本程序不负责！</div>
                        </v-alert>

                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.clear_recyclebin_enabled" label="清空回收站" color="error"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.clear_receive_path_enabled" label="清空最近接收目录"
                              color="error"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-text-field v-model="config.password" label="115访问密码" hint="115网盘安全密码" persistent-hint
                              type="password" density="compact" variant="outlined" hide-details="auto"></v-text-field>
                          </v-col>
                          <v-col cols="12" md="3">
                            <VCronField v-model="config.cron_clear" label="清理周期" hint="设置清理任务的执行周期" persistent-hint
                              density="compact">
                            </VCronField>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-same-playback">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="4">
                            <v-switch v-model="config.same_playback" label="启用" color="info" density="compact"
                              hide-details></v-switch>
                          </v-col>
                        </v-row>

                        <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>多设备同步播放</strong></div>
                          <div class="text-caption">支持多个设备同时播放同一影片</div>
                        </v-alert>
                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-body-2 mb-1"><strong>使用限制</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• 最多支持双IP同时播放</div>
                            <div class="mb-1">• 禁止多IP滥用</div>
                            <div>• 违规操作可能导致账号封禁</div>
                          </div>
                        </v-alert>
                      </v-card-text>
                    </v-window-item>
                  </v-window>
                </v-card-text>
              </v-window-item>

              <!-- 系统配置分类 -->
              <v-window-item value="category-system">
                <v-card-text class="pa-0">
                  <v-tabs v-model="systemSubTab" color="primary" class="sub-category-tabs" slider-color="primary">
                    <v-tab value="tab-cache-config" class="sub-tab">
                      <v-icon size="small" start>mdi-cached</v-icon>缓存配置
                    </v-tab>
                    <v-tab value="tab-data-enhancement" class="sub-tab">
                      <v-icon size="small" start>mdi-database-eye-outline</v-icon>数据增强
                    </v-tab>
                    <v-tab value="tab-advanced-configuration" class="sub-tab">
                      <v-icon size="small" start>mdi-tune</v-icon>高级配置
                    </v-tab>
                  </v-tabs>
                  <v-divider></v-divider>
                  <v-window v-model="systemSubTab" :touch="false">
                    <v-window-item value="tab-cache-config">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12">
                            <v-card variant="outlined" class="mb-4">
                              <v-card-item>
                                <v-card-title class="d-flex align-center">
                                  <v-icon start>mdi-cached</v-icon>
                                  <span class="text-h6">缓存管理</span>
                                </v-card-title>
                              </v-card-item>
                              <v-card-text>
                                <v-alert type="info" variant="tonal" density="compact" class="mb-4"
                                  icon="mdi-information">
                                  <div class="text-caption">缓存清理功能可以帮助您清理插件运行过程中产生的缓存数据，解决部分因缓存导致的问题。</div>
                                </v-alert>

                                <v-row>
                                  <v-col cols="12" md="6">
                                    <v-card variant="outlined" class="pa-4 d-flex flex-column cache-card">
                                      <div class="d-flex align-center mb-3">
                                        <v-icon color="primary" class="mr-2">mdi-folder-cog</v-icon>
                                        <span class="text-subtitle-1 font-weight-medium">清理文件路径ID缓存</span>
                                      </div>
                                      <p class="text-body-2 text-grey-darken-1 mb-3 flex-grow-1">
                                        清理文件路径ID缓存，包括目录ID到路径的映射缓存。
                                      </p>
                                      <v-btn color="primary" variant="outlined" :loading="clearIdPathCacheLoading"
                                        @click="clearIdPathCache" prepend-icon="mdi-folder-cog" block>
                                        清理文件路径ID缓存
                                      </v-btn>
                                    </v-card>
                                  </v-col>

                                  <v-col cols="12" md="6">
                                    <v-card variant="outlined" class="pa-4 d-flex flex-column cache-card">
                                      <div class="d-flex align-center mb-3">
                                        <v-icon color="warning" class="mr-2">mdi-skip-next</v-icon>
                                        <span class="text-subtitle-1 font-weight-medium">清理增量同步跳过路径缓存</span>
                                      </div>
                                      <p class="text-body-2 text-grey-darken-1 mb-3 flex-grow-1">
                                        清理增量同步跳过路径缓存，重置增量同步的跳过路径记录，用于重新处理之前跳过的文件。
                                      </p>
                                      <v-btn color="warning" variant="outlined"
                                        :loading="clearIncrementSkipCacheLoading" @click="clearIncrementSkipCache"
                                        prepend-icon="mdi-skip-next" block>
                                        清理增量同步跳过路径缓存
                                      </v-btn>
                                    </v-card>
                                  </v-col>
                                </v-row>
                              </v-card-text>
                            </v-card>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-data-enhancement">
                      <v-card-text>
                        <v-row>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.error_info_upload" label="错误信息上传" color="info"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.upload_module_enhancement" label="上传模块增强" color="info"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.transfer_module_enhancement" label="整理模块增强" color="info"
                              density="compact" :disabled="isTransferModuleEnhancementLocked" hint="此功能已废弃"
                              persistent-hint></v-switch>
                          </v-col>
                          <v-col cols="12" md="3">
                            <v-switch v-model="config.pan_transfer_takeover" label="接管网盘整理" color="info"
                              density="compact" hint="接管 115 → 115 整理任务进行批量处理，需要存储模块为 115网盘Plus"
                              persistent-hint></v-switch>
                          </v-col>
                        </v-row>
                        <v-row>
                          <v-col cols="12" md="4">
                            <v-switch v-model="config.upload_share_info" label="上传分享链接" color="info"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="4">
                            <v-switch v-model="config.upload_offline_info" label="上传离线下载链接" color="info"
                              density="compact"></v-switch>
                          </v-col>
                          <v-col cols="12" md="4">
                            <v-select v-model="config.storage_module" label="存储模块选择" :items="[
                              { title: '115网盘', value: 'u115' },
                              { title: '115网盘Plus', value: '115网盘Plus' }
                            ]" chips closable-chips
                              :hint="config.pan_transfer_takeover ? '接管网盘整理功能必须使用 115网盘Plus' : '选择使用的存储模块'"
                              persistent-hint></v-select>
                          </v-col>
                        </v-row>
                        <v-row v-if="config.pan_transfer_takeover">
                          <v-col cols="12">
                            <v-alert type="warning" variant="tonal" density="compact" icon="mdi-alert"
                              v-if="config.storage_module !== '115网盘Plus'">
                              <div class="text-body-2 mb-1"><strong>提示：</strong></div>
                              <div class="text-caption">
                                接管网盘整理功能已启用，但当前存储模块为 <strong>{{ config.storage_module === 'u115' ? '115网盘' :
                                  config.storage_module
                                }}</strong>。
                                请将存储模块切换为 <strong>115网盘Plus</strong>，否则接管功能将无法生效。
                              </div>
                            </v-alert>
                            <v-alert type="info" variant="tonal" density="compact" icon="mdi-information" v-else>
                              <div class="text-body-2 mb-1"><strong>功能说明：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">• <strong>接管网盘整理：</strong>启用后将接管 MoviePilot 的 115 → 115
                                  整理任务，使用批量处理提升整理效率
                                </div>
                                <div class="mb-1">• <strong>与整理模块接口增强的区别：</strong>此功能是接管整理流程，而整理模块接口增强是对存储接口的优化</div>
                                <div>• 当前存储模块为 <strong>115网盘Plus</strong>，功能可以正常使用</div>
                              </div>
                            </v-alert>
                          </v-col>
                        </v-row>
                        <v-row>
                          <v-col cols="12" md="4" class="d-flex align-center">
                            <v-btn @click="getMachineId" size="small" prepend-icon="mdi-identifier">显示设备ID</v-btn>
                          </v-col>
                        </v-row>

                        <v-row v-if="machineId">
                          <v-col cols="12">
                            <v-text-field v-model="machineId" label="Machine ID" readonly density="compact"
                              variant="outlined" hide-details="auto"></v-text-field>
                          </v-col>
                        </v-row>

                        <!-- 上传模块增强配置 -->
                        <v-expansion-panels variant="tonal" class="mt-6">
                          <v-expansion-panel>
                            <v-expansion-panel-title>
                              <v-icon icon="mdi-tune-variant" class="mr-2"></v-icon>
                              上传模块增强配置
                            </v-expansion-panel-title>
                            <v-expansion-panel-text class="pa-4">
                              <v-row>
                                <v-col cols="12" md="4">
                                  <v-switch v-model="config.upload_module_skip_slow_upload" label="秒传失败直接退出"
                                    color="info" density="compact"></v-switch>
                                </v-col>
                                <v-col cols="12" md="4">
                                  <v-switch v-model="config.upload_module_notify" label="秒传等待发送通知" color="info"
                                    density="compact"></v-switch>
                                </v-col>
                                <v-col cols="12" md="4">
                                  <v-switch v-model="config.upload_open_result_notify" label="上传结果通知" color="info"
                                    density="compact"></v-switch>
                                </v-col>
                              </v-row>
                              <v-row>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model.number="config.upload_module_wait_time" label="秒传休眠等待时间（单位秒）"
                                    type="number" hint="秒传休眠等待时间（单位秒）" persistent-hint density="compact"></v-text-field>
                                </v-col>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model.number="config.upload_module_wait_timeout" label="秒传最长等待时间（单位秒）"
                                    type="number" hint="秒传最长等待时间（单位秒）" persistent-hint density="compact"></v-text-field>
                                </v-col>
                              </v-row>
                              <v-row>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model="skipUploadWaitSizeFormatted" label="跳过等待秒传的文件大小阈值"
                                    hint="文件小于此值将跳过等待秒传（单位支持K，M，G）" persistent-hint density="compact"
                                    placeholder="例如: 5M, 1.5G (可为空)" clearable></v-text-field>
                                </v-col>
                                <v-col cols="12" md="6">
                                  <v-text-field v-model="forceUploadWaitSizeFormatted" label="强制等待秒传的文件大小阈值"
                                    hint="文件大于此值将强制等待秒传（单位支持K，M，G）" persistent-hint density="compact"
                                    placeholder="例如: 5M, 1.5G (可为空)" clearable></v-text-field>
                                </v-col>
                              </v-row>
                              <v-row v-if="config.upload_module_skip_slow_upload">
                                <v-col cols="12" md="6">
                                  <v-text-field v-model="skipSlowUploadSizeFormatted" label="秒传失败后跳过上传的文件大小阈值"
                                    hint="秒传失败后，大于等于此值的文件将跳过上传，小于此值的文件将继续上传（单位支持K，M，G）" persistent-hint
                                    density="compact" placeholder="例如: 100M, 1G (可为空)" clearable></v-text-field>
                                </v-col>
                              </v-row>
                              <v-alert type="info" variant="tonal" density="compact" class="mt-3"
                                icon="mdi-information">
                                <div class="text-body-2 mb-1"><strong>秒传失败直接退出：</strong></div>
                                <div class="text-caption">此功能开启后，对于无法秒传或者秒传等待超时的文件将直接跳过上传步骤，整理返回失败。</div>
                                <div class="text-caption mt-1">
                                  如果设置了"秒传失败后跳过上传的文件大小阈值"，则只有大于等于该阈值的文件才会跳过上传，小于该阈值的文件将继续执行上传。
                                </div>
                              </v-alert>
                            </v-expansion-panel-text>
                          </v-expansion-panel>
                        </v-expansion-panels>

                        <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
                          <div class="text-body-2 mb-1"><strong>115上传增强有效范围：</strong></div>
                          <div class="text-caption">此功能开启后，将对整个MoviePilot系统内所有调用115网盘上传的功能生效。</div>
                        </v-alert>

                        <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
                          <div class="text-body-2 mb-1"><strong>风险与免责声明</strong></div>
                          <div class="text-caption">
                            <div class="mb-1">• 插件程序内包含可选的Sentry分析组件，详见<a href="https://sentry.io/privacy/"
                                target="_blank" style="color: inherit; text-decoration: underline;">Sentry Privacy
                                Policy</a></div>
                            <div class="mb-1">• 插件程序将在必要时上传错误信息及运行环境信息</div>
                            <div>• 插件程序将记录程序运行重要节点并保存追踪数据至少72小时</div>
                          </div>
                        </v-alert>
                      </v-card-text>
                    </v-window-item>
                    <v-window-item value="tab-advanced-configuration">
                      <v-card-text>
                        <!-- STRM URL 自定义模板 -->
                        <v-row>
                          <v-col cols="12">
                            <v-switch v-model="config.strm_url_template_enabled" label="启用 STRM URL 自定义模板 (Jinja2)"
                              color="primary" density="compact" hint="启用后可以使用 Jinja2 模板语法自定义 STRM 文件的 URL 格式"
                              persistent-hint></v-switch>
                          </v-col>
                        </v-row>

                        <v-expand-transition>
                          <div v-if="config.strm_url_template_enabled">
                            <v-alert type="info" variant="tonal" density="compact" class="mt-2 mb-3"
                              icon="mdi-information">
                              <div class="text-body-2 mb-1"><strong>STRM URL 生成优先级：</strong></div>
                              <div class="text-caption">
                                <div class="mb-1">1. <strong>URL 自定义模板</strong>（如果启用）：优先使用 Jinja2 模板渲染</div>
                                <div class="mb-1">2. <strong>FUSE STRM 接管</strong>（如果启用且匹配规则）：生成指向挂载路径的 STRM 内容</div>
                                <div>3. <strong>默认格式</strong>：使用基础设置中的「STRM文件URL格式」和「STRM URL 文件名称编码」</div>
                              </div>
                            </v-alert>
                            <v-row class="mt-2">
                              <v-col cols="12">
                                <v-textarea v-model="config.strm_url_template" label="STRM URL 基础模板 (Jinja2)"
                                  hint="支持 Jinja2 语法，可用变量和过滤器见下方说明" persistent-hint rows="4" variant="outlined"
                                  density="compact"
                                  placeholder="{{ base_url }}?pickcode={{ pickcode }}{% if file_name %}&file_name={{ file_name | urlencode }}{% endif %}"
                                  clearable></v-textarea>
                              </v-col>
                            </v-row>

                            <v-row class="mt-2">
                              <v-col cols="12">
                                <v-textarea v-model="config.strm_url_template_custom" label="STRM URL 扩展名特定模板 (Jinja2)"
                                  hint="为特定文件扩展名指定 URL 模板，优先级高于基础模板。格式：ext1,ext2 => template（每行一个）" persistent-hint
                                  rows="5" variant="outlined" density="compact"
                                  placeholder="例如：&#10;mkv,mp4 => {{ base_url }}?pickcode={{ pickcode }}&file_name={{ file_name | urlencode }}&file_path={{ file_path | path_encode }}&#10;iso => {{ base_url }}?pickcode={{ pickcode }}&file_name={{ file_name | urlencode }}"
                                  clearable></v-textarea>
                              </v-col>
                            </v-row>

                            <v-card variant="outlined" class="mt-3" color="info" v-pre>
                              <v-card-text class="pa-3">
                                <div class="mb-3">
                                  <div class="d-flex align-center mb-2">
                                    <v-icon icon="mdi-information" size="small" class="mr-2" color="info"></v-icon>
                                    <strong class="text-body-2">可用变量</strong>
                                  </div>
                                  <div class="ml-6">
                                    <div class="mb-1"><code class="text-caption">base_url</code> - 基础 URL</div>
                                    <div class="mb-1"><code class="text-caption">pickcode</code> - 文件 pickcode（仅普通 STRM）
                                    </div>
                                    <div class="mb-1"><code class="text-caption">share_code</code> - 分享码（仅分享 STRM）</div>
                                    <div class="mb-1"><code class="text-caption">receive_code</code> - 提取码（仅分享 STRM）
                                    </div>
                                    <div class="mb-1"><code class="text-caption">file_id</code> - 文件 ID</div>
                                    <div class="mb-1"><code class="text-caption">file_name</code> - 文件名称</div>
                                    <div class="mb-1"><code class="text-caption">file_path</code> - 文件网盘路径</div>
                                  </div>
                                </div>

                                <v-divider class="my-3"></v-divider>

                                <div class="mb-3">
                                  <div class="d-flex align-center mb-2">
                                    <v-icon icon="mdi-filter" size="small" class="mr-2" color="info"></v-icon>
                                    <strong class="text-body-2">可用过滤器</strong>
                                  </div>
                                  <div class="ml-6">
                                    <div class="mb-1"><code class="text-caption">urlencode</code> - URL 编码（如：<code
                                        class="text-caption">{{ file_name | urlencode }}</code>）</div>
                                    <div class="mb-1"><code class="text-caption">path_encode</code> - 路径编码，保留斜杠（如：<code
                                        class="text-caption">{{ file_path | path_encode }}</code>）</div>
                                    <div class="mb-1"><code class="text-caption">upper</code> - 转大写</div>
                                    <div class="mb-1"><code class="text-caption">lower</code> - 转小写</div>
                                    <div class="mb-1"><code class="text-caption">default</code> - 默认值（如：<code
                                        class="text-caption">{{
                              file_name | default('unknown') }}</code>）</div>
                                  </div>
                                </div>

                                <v-divider class="my-3"></v-divider>

                                <div>
                                  <div class="d-flex align-center mb-2">
                                    <v-icon icon="mdi-code-tags" size="small" class="mr-2" color="info"></v-icon>
                                    <strong class="text-body-2">模板示例</strong>
                                  </div>
                                  <div class="ml-6">
                                    <div class="mb-2">
                                      <div class="text-caption text-medium-emphasis mb-1">普通 STRM:</div>
                                      <code class="text-caption pa-2 d-block"
                                        style="background-color: rgba(var(--v-theme-on-surface), 0.05); border-radius: 8px; font-family: 'Courier New', monospace; word-break: break-all; display: block; white-space: pre-wrap; border: 1px solid rgba(var(--v-theme-on-surface), 0.12); padding: 10px;">{{
                              base_url }}?pickcode={{ pickcode }}{% if file_name %}&file_name={{ file_name
                              | urlencode }}{% endif %}</code>
                                    </div>
                                    <div>
                                      <div class="text-caption text-medium-emphasis mb-1">分享 STRM:</div>
                                      <code class="text-caption pa-2 d-block"
                                        style="background-color: rgba(var(--v-theme-on-surface), 0.05); border-radius: 8px; font-family: 'Courier New', monospace; word-break: break-all; display: block; white-space: pre-wrap; border: 1px solid rgba(var(--v-theme-on-surface), 0.12); padding: 10px;">{{
                              base_url }}?share_code={{ share_code }}&receive_code={{ receive_code
                              }}&id={{ file_id }}{% if file_name %}&file_name={{ file_name | urlencode }}{% endif
                              %}</code>
                                    </div>
                                  </div>
                                </div>
                              </v-card-text>
                            </v-card>
                          </div>
                        </v-expand-transition>

                        <!-- STRM 文件名自定义模板 -->
                        <v-divider class="my-6"></v-divider>

                        <v-row class="mt-4">
                          <v-col cols="12">
                            <v-switch v-model="config.strm_filename_template_enabled" label="启用 STRM 文件名自定义模板 (Jinja2)"
                              color="primary" density="compact" hint="启用后可以使用 Jinja2 模板语法自定义 STRM 文件的文件名格式"
                              persistent-hint></v-switch>
                          </v-col>
                        </v-row>

                        <v-expand-transition>
                          <div v-if="config.strm_filename_template_enabled">
                            <v-row class="mt-2">
                              <v-col cols="12">
                                <v-textarea v-model="config.strm_filename_template" label="STRM 文件名基础模板 (Jinja2)"
                                  hint="支持 Jinja2 语法，可用变量和过滤器见下方说明" persistent-hint rows="3" variant="outlined"
                                  density="compact" placeholder="{{ file_stem }}.strm" clearable></v-textarea>
                              </v-col>
                            </v-row>

                            <v-row class="mt-2">
                              <v-col cols="12">
                                <v-textarea v-model="config.strm_filename_template_custom"
                                  label="STRM 文件名扩展名特定模板 (Jinja2)"
                                  hint="为特定文件扩展名指定文件名模板，优先级高于基础模板。格式：ext1,ext2 => template（每行一个）" persistent-hint
                                  rows="4" variant="outlined" density="compact"
                                  placeholder="例如：&#10;iso => {{ file_stem }}.iso.strm&#10;mkv,mp4 => {{ file_stem | upper }}.strm"
                                  clearable></v-textarea>
                              </v-col>
                            </v-row>

                            <v-card variant="outlined" class="mt-3" color="info" v-pre>
                              <v-card-text class="pa-3">
                                <div class="mb-3">
                                  <div class="d-flex align-center mb-2">
                                    <v-icon icon="mdi-information" size="small" class="mr-2" color="info"></v-icon>
                                    <strong class="text-body-2">可用变量</strong>
                                  </div>
                                  <div class="ml-6">
                                    <div class="mb-1"><code class="text-caption">file_name</code> - 完整文件名（包含扩展名）</div>
                                    <div class="mb-1"><code class="text-caption">file_stem</code> - 文件名（不含扩展名）</div>
                                    <div class="mb-1"><code class="text-caption">file_suffix</code> - 文件扩展名（包含点号，如 .mkv）
                                    </div>
                                  </div>
                                </div>

                                <v-divider class="my-3"></v-divider>

                                <div class="mb-3">
                                  <div class="d-flex align-center mb-2">
                                    <v-icon icon="mdi-filter" size="small" class="mr-2" color="info"></v-icon>
                                    <strong class="text-body-2">可用过滤器</strong>
                                  </div>
                                  <div class="ml-6">
                                    <div class="mb-1"><code class="text-caption">upper</code> - 转大写（如：<code
                                        class="text-caption">{{
                              file_stem | upper }}</code>）</div>
                                    <div class="mb-1"><code class="text-caption">lower</code> - 转小写（如：<code
                                        class="text-caption">{{
                              file_stem | lower }}</code>）</div>
                                    <div class="mb-1"><code class="text-caption">sanitize</code> - 清理文件名中的非法字符（如：<code
                                        class="text-caption">{{ file_name | sanitize }}</code>）</div>
                                    <div class="mb-1"><code class="text-caption">default</code> - 默认值（如：<code
                                        class="text-caption">{{
                              file_stem | default('unknown') }}</code>）</div>
                                  </div>
                                </div>

                                <v-divider class="my-3"></v-divider>

                                <div class="mb-3">
                                  <div class="d-flex align-center mb-2">
                                    <v-icon icon="mdi-code-tags" size="small" class="mr-2" color="info"></v-icon>
                                    <strong class="text-body-2">模板示例</strong>
                                  </div>
                                  <div class="ml-6">
                                    <div class="mb-2">
                                      <div class="text-caption text-medium-emphasis mb-1">默认格式:</div>
                                      <code class="text-caption pa-2 d-block"
                                        style="background-color: rgba(var(--v-theme-on-surface), 0.05); border-radius: 8px; font-family: 'Courier New', monospace; display: block; border: 1px solid rgba(var(--v-theme-on-surface), 0.12); padding: 10px;">{{
                              file_stem }}.strm</code>
                                    </div>
                                    <div class="mb-2">
                                      <div class="text-caption text-medium-emphasis mb-1">ISO 格式:</div>
                                      <code class="text-caption pa-2 d-block"
                                        style="background-color: rgba(var(--v-theme-on-surface), 0.05); border-radius: 8px; font-family: 'Courier New', monospace; display: block; border: 1px solid rgba(var(--v-theme-on-surface), 0.12); padding: 10px;">{{
                              file_stem }}.iso.strm</code>
                                    </div>
                                    <div>
                                      <div class="text-caption text-medium-emphasis mb-1">大写文件名:</div>
                                      <code class="text-caption pa-2 d-block"
                                        style="background-color: rgba(var(--v-theme-on-surface), 0.05); border-radius: 8px; font-family: 'Courier New', monospace; display: block; border: 1px solid rgba(var(--v-theme-on-surface), 0.12); padding: 10px;">{{
                              file_stem | upper }}.strm</code>
                                    </div>
                                  </div>
                                </div>

                                <v-divider class="my-3"></v-divider>

                                <div>
                                  <div class="d-flex align-center mb-2">
                                    <v-icon icon="mdi-alert-circle-outline" size="small" class="mr-2"
                                      color="warning"></v-icon>
                                    <strong class="text-body-2">注意事项</strong>
                                  </div>
                                  <div class="ml-6">
                                    <div class="mb-1 text-caption">• 模板渲染后的文件名会自动清理非法字符（&lt;&gt;:&quot;/\\|?*）</div>
                                    <div class="mb-1 text-caption">• 建议模板以 .strm 结尾，确保生成的文件具有正确的扩展名</div>
                                    <div class="text-caption">• 如果模板未指定扩展名，系统会自动添加 .strm</div>
                                  </div>
                                </div>
                              </v-card-text>
                            </v-card>
                          </div>
                        </v-expand-transition>

                        <v-divider class="my-6"></v-divider>

                        <v-row class="mt-4">
                          <v-col cols="12">
                            <v-combobox v-model="config.strm_generate_blacklist" label="STRM文件关键词过滤黑名单"
                              hint="输入关键词后按回车确认，可添加多个。包含这些词的视频文件将不会生成STRM文件。" persistent-hint multiple chips
                              closable-chips variant="outlined" density="compact"></v-combobox>
                          </v-col>
                        </v-row>

                        <v-row class="mt-4">
                          <v-col cols="12">
                            <v-combobox v-model="config.mediainfo_download_whitelist" label="媒体信息文件下载关键词过滤白名单"
                              hint="输入关键词后按回车确认，可添加多个。不包含这些词的媒体信息文件将不会下载。" persistent-hint multiple chips closable-chips
                              variant="outlined" density="compact"></v-combobox>
                          </v-col>
                        </v-row>

                        <v-row class="mt-4">
                          <v-col cols="12">
                            <v-combobox v-model="config.mediainfo_download_blacklist" label="媒体信息文件下载关键词过滤黑名单"
                              hint="输入关键词后按回车确认，可添加多个。包含这些词的媒体信息文件将不会下载。" persistent-hint multiple chips closable-chips
                              variant="outlined" density="compact"></v-combobox>
                          </v-col>
                        </v-row>

                        <v-divider class="my-6"></v-divider>

                        <v-row class="mt-4">
                          <v-col cols="12" md="4">
                            <v-switch v-model="config.strm_url_encode" label="STRM URL 文件名称编码" color="info"
                              density="compact"
                              :hint="config.strm_url_template_enabled ? '已启用自定义模板时优先使用模板，模板渲染失败时将使用此设置作为后备方案。在模板中可使用 urlencode 过滤器进行编码。' : '启用后，STRM文件中的URL会对文件名进行编码处理'"
                              persistent-hint></v-switch>
                          </v-col>
                        </v-row>
                      </v-card-text>
                    </v-window-item>
                  </v-window>
                </v-card-text>
              </v-window-item>
            </v-window>
          </v-card>

          <!-- 操作按钮 -->

        </div>
      </v-card-text>
      <v-card-actions class="px-3 py-2 d-flex" style="flex-shrink: 0;">
        <v-btn color="warning" variant="text" @click="emit('switch')" size="small" prepend-icon="mdi-arrow-left">
          返回
        </v-btn>
        <v-spacer></v-spacer>
        <v-btn color="primary" variant="text" @click="openDonateDialog" size="small" prepend-icon="mdi-gift">
          捐赠
        </v-btn>
        <v-btn color="warning" variant="text" @click="fullSyncConfirmDialog = true" size="small"
          prepend-icon="mdi-sync">
          全量同步
        </v-btn>
        <v-btn color="success" variant="text" @click="saveConfig" :loading="saveLoading" size="small"
          prepend-icon="mdi-content-save">
          保存配置
        </v-btn>
      </v-card-actions>
    </v-card>

    <!-- 全量同步确认对话框 -->
    <v-dialog v-model="fullSyncConfirmDialog" max-width="500" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="warning" class="mr-2"></v-icon>
          确认操作
        </v-card-title>
        <v-card-text>
          <div class="mb-2">您确定要立即执行全量同步吗？</div>
          <v-alert v-if="config.full_sync_media_server_refresh_enabled" type="warning" variant="tonal" density="compact"
            class="mt-2" icon="mdi-alert">
            <div class="text-body-2 mb-1"><strong>重要警告</strong></div>
            <div class="text-caption">
              全量同步完成后将自动刷新整个媒体库，此操作会扫描所有媒体文件，可能导致媒体服务器负载增加。请确保您已了解此风险并自行承担相应责任。
            </div>
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer></v-spacer>
          <v-btn color="grey" variant="text" @click="fullSyncConfirmDialog = false" :disabled="syncLoading">
            取消
          </v-btn>
          <v-btn color="warning" variant="text" @click="handleConfirmFullSync" :loading="syncLoading">
            确认执行
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 目录选择器对话框 -->
    <v-dialog v-model="dirDialog.show" max-width="800">
      <v-card>
        <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon :icon="dirDialog.isLocal ? 'mdi-folder-search' : 'mdi-folder-network'" class="mr-2" color="primary" />
          <span>{{ dirDialog.isLocal ? '选择本地目录' : '选择网盘目录' }}</span>
        </v-card-title>

        <v-card-text class="px-3 py-2">
          <div v-if="dirDialog.loading" class="d-flex justify-center my-3">
            <v-progress-circular indeterminate color="primary"></v-progress-circular>
          </div>

          <div v-else>
            <!-- 当前路径显示 -->
            <v-text-field v-model="dirDialog.currentPath" label="当前路径" variant="outlined" density="compact" class="mb-2"
              @keyup.enter="loadDirContent"></v-text-field>

            <!-- 文件列表 -->
            <v-list class="border rounded" max-height="300px" overflow-y="auto">
              <v-list-item
                v-if="dirDialog.currentPath !== '/' && dirDialog.currentPath !== 'C:\\' && dirDialog.currentPath !== 'C:/'"
                @click="navigateToParentDir" class="py-1">
                <template v-slot:prepend>
                  <v-icon icon="mdi-arrow-up" size="small" class="mr-2" color="grey" />
                </template>
                <v-list-item-title class="text-body-2">上级目录</v-list-item-title>
                <v-list-item-subtitle>..</v-list-item-subtitle>
              </v-list-item>

              <v-list-item v-for="(item, index) in dirDialog.items" :key="index" @click="selectDir(item)"
                :disabled="!item.is_dir" class="py-1">
                <template v-slot:prepend>
                  <v-icon :icon="item.is_dir ? 'mdi-folder' : 'mdi-file'" size="small" class="mr-2"
                    :color="item.is_dir ? 'amber-darken-2' : 'blue'" />
                </template>
                <v-list-item-title class="text-body-2">{{ item.name }}</v-list-item-title>
              </v-list-item>

              <v-list-item v-if="!dirDialog.items.length" class="py-2 text-center">
                <v-list-item-title class="text-body-2 text-grey">该目录为空或访问受限</v-list-item-title>
              </v-list-item>
            </v-list>
          </div>

          <v-alert v-if="dirDialog.error" type="error" density="compact" class="mt-2 text-caption" variant="tonal">
            {{ dirDialog.error }}
          </v-alert>
        </v-card-text>

        <v-card-actions class="px-3 py-2">
          <v-spacer></v-spacer>
          <v-btn color="primary" @click="confirmDirSelection" :disabled="!dirDialog.currentPath || dirDialog.loading"
            variant="text" size="small">
            选择当前目录
          </v-btn>
          <v-btn color="grey" @click="closeDirDialog" variant="text" size="small">
            取消
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-model="manualTransferDialog.show" max-width="500" persistent>
      <v-card>
        <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon icon="mdi-play-circle" class="mr-2" color="primary" size="small" />
          <span>手动整理确认</span>
        </v-card-title>

        <v-card-text class="px-3 py-3">
          <div v-if="!manualTransferDialog.loading && !manualTransferDialog.result">
            <div class="text-body-2 mb-3">确定要手动整理以下目录吗？</div>
            <v-alert type="info" variant="tonal" density="compact" icon="mdi-information">
              <div class="text-body-2">
                <strong>路径：</strong>{{ manualTransferDialog.path }}
              </div>
            </v-alert>
          </div>

          <div v-else-if="manualTransferDialog.loading" class="d-flex flex-column align-center py-3">
            <v-progress-circular indeterminate color="primary" size="48" class="mb-3"></v-progress-circular>
            <div class="text-body-2 text-grey">正在启动整理任务...</div>
          </div>

          <div v-else-if="manualTransferDialog.result">
            <v-alert :type="manualTransferDialog.result.type" variant="tonal" density="compact"
              :icon="manualTransferDialog.result.type === 'success' ? 'mdi-check-circle' : 'mdi-alert-circle'">
              <div class="text-subtitle-2 mb-1">
                {{ manualTransferDialog.result.title }}
              </div>
              <div class="text-body-2">{{ manualTransferDialog.result.message }}</div>
            </v-alert>
          </div>
        </v-card-text>

        <v-card-actions class="px-3 py-2">
          <v-spacer></v-spacer>
          <template v-if="!manualTransferDialog.loading && !manualTransferDialog.result">
            <v-btn color="grey" variant="text" @click="closeManualTransferDialog" size="small">
              取消
            </v-btn>
            <v-btn color="primary" variant="text" @click="confirmManualTransfer" size="small">
              确认执行
            </v-btn>
          </template>
          <template v-else>
            <v-btn color="primary" variant="text" @click="closeManualTransferDialog" size="small">
              关闭
            </v-btn>
          </template>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 二维码登录对话框 -->
    <v-dialog v-model="qrDialog.show" max-width="450">
      <v-card>
        <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon icon="mdi-qrcode" class="mr-2" color="primary" size="small" />
          <span>115网盘扫码登录</span>
        </v-card-title>

        <v-card-text class="text-center py-4">
          <v-alert v-if="qrDialog.error" type="error" density="compact" class="mb-3 mx-3" variant="tonal" closable>
            {{ qrDialog.error }}
          </v-alert>

          <div v-if="qrDialog.loading" class="d-flex flex-column align-center py-3">
            <v-progress-circular indeterminate color="primary" class="mb-3"></v-progress-circular>
            <div>正在获取二维码...</div>
          </div>

          <div v-else-if="qrDialog.qrcode" class="d-flex flex-column align-center">
            <div class="mb-2 font-weight-medium">请选择扫码方式</div>
            <v-chip-group v-model="qrDialog.clientType" class="mb-3" mandatory selected-class="primary">
              <v-chip v-for="type in clientTypes" :key="type.value" :value="type.value" variant="outlined"
                color="primary" size="small">
                {{ type.label }}
              </v-chip>
            </v-chip-group>
            <div class="d-flex flex-column align-center mb-3">
              <v-card flat class="border pa-2 mb-2">
                <img :src="qrDialog.qrcode" width="220" height="220" />
              </v-card>
              <div class="text-body-2 text-grey mb-1">{{ qrDialog.tips }}</div>
              <div class="text-subtitle-2 font-weight-medium text-primary">{{ qrDialog.status }}</div>
            </div>
            <v-btn color="primary" variant="tonal" @click="refreshQrCode" size="small" class="mb-2">
              <v-icon left size="small" class="mr-1">mdi-refresh</v-icon>刷新二维码
            </v-btn>
          </div>

          <div v-else class="d-flex flex-column align-center py-3">
            <v-icon icon="mdi-qrcode-off" size="64" color="grey" class="mb-3"></v-icon>
            <div class="text-subtitle-1">二维码获取失败</div>
            <div class="text-body-2 text-grey">请点击刷新按钮重试</div>
            <div class="text-caption mt-2 text-grey">
              <v-icon icon="mdi-alert-circle" size="small" class="mr-1 text-warning"></v-icon>
              如果多次获取失败，请检查网络连接
            </div>
          </div>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions class="px-3 py-2">
          <v-btn color="grey" variant="text" @click="closeQrDialog" size="small" prepend-icon="mdi-close">关闭</v-btn>
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="text" @click="refreshQrCode" :disabled="qrDialog.loading" size="small"
            prepend-icon="mdi-refresh">
            刷新二维码
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 阿里云盘二维码登录对话框 -->
    <v-dialog v-model="aliQrDialog.show" max-width="450">
      <v-card>
        <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon icon="mdi-qrcode" class="mr-2" color="primary" size="small" />
          <span>阿里云盘扫码登录</span>
        </v-card-title>
        <v-card-text class="text-center py-4">
          <v-alert v-if="aliQrDialog.error" type="error" density="compact" class="mb-3 mx-3" variant="tonal" closable>
            {{ aliQrDialog.error }}
          </v-alert>
          <div v-if="aliQrDialog.loading" class="d-flex flex-column align-center py-3">
            <v-progress-circular indeterminate color="primary" class="mb-3"></v-progress-circular>
            <div>正在获取二维码...</div>
          </div>
          <div v-else-if="aliQrDialog.qrcode" class="d-flex flex-column align-center">
            <v-card flat class="border pa-2 mb-2">
              <img :src="aliQrDialog.qrcode" width="220" height="220" />
            </v-card>
            <div class="text-body-2 text-grey mb-1">请使用阿里云盘App扫描二维码</div>
            <div class="text-subtitle-2 font-weight-medium text-primary">{{ aliQrDialog.status }}</div>
          </div>
          <div v-else class="d-flex flex-column align-center py-3">
            <v-icon icon="mdi-qrcode-off" size="64" color="grey" class="mb-3"></v-icon>
            <div class="text-subtitle-1">二维码获取失败</div>
            <div class="text-body-2 text-grey">请点击刷新按钮重试</div>
          </div>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions class="px-3 py-2">
          <v-btn color="grey" variant="text" @click="closeAliQrCodeDialog" size="small"
            prepend-icon="mdi-close">关闭</v-btn>
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="text" @click="refreshAliQrCode" :disabled="aliQrDialog.loading" size="small"
            prepend-icon="mdi-refresh">
            刷新
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 捐赠对话框 -->
    <v-dialog v-model="donateDialog.show" max-width="600">
      <v-card>
        <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon icon="mdi-gift" class="mr-2" color="primary" size="small" />
          <span>支持作者 / 获取授权</span>
        </v-card-title>

        <v-card-text class="py-4">
          <v-alert type="success" variant="tonal" density="compact" class="mb-3" icon="mdi-information-outline">
            <span class="text-body-2">无论是否捐赠，插件功能均可正常使用。捐赠为自愿支持行为。</span>
          </v-alert>
          <v-alert v-if="donateDialog.error" type="error" density="compact" class="mb-3" variant="tonal" closable>
            {{ donateDialog.error }}
          </v-alert>

          <div v-if="donateDialog.loading" class="d-flex flex-column align-center py-3">
            <v-progress-circular indeterminate color="primary" class="mb-3"></v-progress-circular>
            <div>正在加载捐赠信息...</div>
          </div>

          <div v-else-if="donateDialog.donateInfo">
            <!-- 授权状态 -->
            <v-card variant="outlined" class="mb-4">
              <v-card-item>
                <v-card-title class="d-flex align-center">
                  <v-icon icon="mdi-shield-check"
                    :color="donateDialog.authorizationStatus?.is_authorized ? 'success' : 'warning'"
                    class="mr-2"></v-icon>
                  <span class="text-h6">授权状态</span>
                </v-card-title>
              </v-card-item>
              <v-card-text>
                <v-row v-if="!donateDialog.authorizationStatus">
                  <v-col cols="12">
                    <div class="text-caption text-grey">暂无授权信息</div>
                  </v-col>
                </v-row>
                <v-row v-else>
                  <v-col cols="12" md="4">
                    <div class="d-flex flex-column">
                      <span class="text-caption text-grey-darken-1">授权状态</span>
                      <v-chip :color="donateDialog.authorizationStatus.is_authorized ? 'success' : 'warning'"
                        size="small" class="mt-1">
                        {{ donateDialog.authorizationStatus.is_authorized ? '已授权' : '未授权' }}
                      </v-chip>
                    </div>
                  </v-col>
                  <v-col cols="12" md="4" v-if="donateDialog.authorizationStatus.is_authorized">
                    <div class="d-flex flex-column">
                      <span class="text-caption text-grey-darken-1">授权类型</span>
                      <v-chip :color="donateDialog.authorizationStatus.is_permanent ? 'info' : 'default'" size="small"
                        class="mt-1">
                        {{ donateDialog.authorizationStatus.is_permanent ? '永久授权' : '临时授权' }}
                      </v-chip>
                    </div>
                  </v-col>
                  <v-col cols="12" md="4"
                    v-if="donateDialog.authorizationStatus.is_authorized && !donateDialog.authorizationStatus.is_permanent && donateDialog.authorizationStatus.authorization_expiration">
                    <div class="d-flex flex-column">
                      <span class="text-caption text-grey-darken-1">过期时间</span>
                      <span class="text-body-2 mt-1">{{
                        formatAuthorizationExpiration(donateDialog.authorizationStatus.authorization_expiration)
                      }}</span>
                    </div>
                  </v-col>
                </v-row>
              </v-card-text>
            </v-card>

            <v-alert type="info" variant="tonal" density="compact" class="mb-4" icon="mdi-information">
              <div class="text-caption">
                捐赠后前往 <a :href="`https://${donateDialog.donateInfo.telegram_bot}`" target="_blank"
                  style="color: inherit; text-decoration: underline;">Telegram Bot</a> 使用
                <code>{{ donateDialog.donateInfo.donate_command }}</code> 命令提交捐赠证明
              </div>
            </v-alert>

            <!-- 捐赠方式选择 -->
            <v-tabs v-model="donateDialog.activeTab" color="primary" class="mb-4">
              <v-tab v-if="donateDialog.donateInfo.wechat?.enabled" value="wechat">
                <v-icon start>mdi-wechat</v-icon>微信
              </v-tab>
              <v-tab v-if="donateDialog.donateInfo.alipay?.enabled" value="alipay">
                <v-icon start>mdi-wallet</v-icon>支付宝
              </v-tab>
            </v-tabs>

            <v-window v-model="donateDialog.activeTab" :touch="false">
              <!-- 微信捐赠 -->
              <v-window-item v-if="donateDialog.donateInfo.wechat?.enabled" value="wechat">
                <div class="d-flex flex-column align-center">
                  <v-card flat class="border pa-2 mb-3">
                    <img v-if="donateDialog.donateInfo.wechat.qrcode" :src="donateDialog.donateInfo.wechat.qrcode"
                      width="280" height="280" alt="微信捐赠码" />
                    <div v-else class="d-flex flex-column align-center pa-8">
                      <v-icon icon="mdi-image-off" size="64" color="grey" class="mb-2"></v-icon>
                      <div class="text-caption text-grey">二维码未配置</div>
                    </div>
                  </v-card>
                  <div class="text-body-2 text-grey">使用微信扫描二维码进行捐赠</div>
                </div>
              </v-window-item>

              <!-- 支付宝捐赠 -->
              <v-window-item v-if="donateDialog.donateInfo.alipay?.enabled" value="alipay">
                <div class="d-flex flex-column align-center">
                  <v-card flat class="border pa-2 mb-3">
                    <img v-if="donateDialog.donateInfo.alipay.qrcode" :src="donateDialog.donateInfo.alipay.qrcode"
                      width="280" height="280" alt="支付宝收款码" />
                    <div v-else class="d-flex flex-column align-center pa-8">
                      <v-icon icon="mdi-image-off" size="64" color="grey" class="mb-2"></v-icon>
                      <div class="text-caption text-grey">二维码未配置</div>
                    </div>
                  </v-card>
                  <div class="text-body-2 text-grey mb-2">使用支付宝扫描二维码进行捐赠</div>

                  <!-- 支付宝红包口令方式 -->
                  <div v-if="donateDialog.donateInfo.alipay?.redpack_enabled" class="w-100 mt-3">
                    <v-divider class="mb-3"></v-divider>
                    <v-alert type="info" variant="tonal" density="compact" icon="mdi-wallet-giftcard">
                      <div class="text-caption">
                        也可以使用支付宝红包口令，将口令发给机器人即可
                      </div>
                    </v-alert>
                  </div>
                </div>
              </v-window-item>
            </v-window>
          </div>

          <div v-else class="d-flex flex-column align-center py-3">
            <v-icon icon="mdi-gift-off" size="64" color="grey" class="mb-3"></v-icon>
            <div class="text-subtitle-1">捐赠信息未配置</div>
            <div class="text-body-2 text-grey">请联系作者获取捐赠方式</div>
          </div>
        </v-card-text>

        <v-divider></v-divider>
        <v-card-actions class="px-3 py-2">
          <v-btn color="grey" variant="text" @click="closeDonateDialog" size="small" prepend-icon="mdi-close">
            关闭
          </v-btn>
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="text"
            :href="`https://${donateDialog.donateInfo?.telegram_bot || 't.me/dds_oof_bot'}`" target="_blank"
            size="small" prepend-icon="mdi-telegram">
            前往 Telegram Bot
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- emby2Alist 配置生成对话框 -->
    <v-dialog v-model="configGeneratorDialog.show" max-width="900" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon icon="mdi-code-tags" class="mr-2" color="primary" size="small" />
          <span>生成 emby2Alist 配置</span>
        </v-card-title>
        <v-card-text class="pa-4">
          <v-alert type="info" variant="tonal" density="compact" class="mb-4" icon="mdi-information">
            <div class="text-body-2 mb-1"><strong>使用说明：</strong></div>
            <div class="text-caption">
              <div class="mb-1">1. 此配置用于 emby2Alist 插件的 <code>mediaPathMapping</code> 规则</div>
              <div class="mb-1">2. 将生成的配置复制到 emby2Alist 的配置文件中</div>
              <div>3. 配置会自动匹配 strm 文件中的 <code>/emby/115</code> 路径并替换为插件重定向地址</div>
            </div>
          </v-alert>

          <v-row>
            <v-col cols="12">
              <v-text-field v-model="configGeneratorDialog.mountDir" label="媒体服务器网盘挂载目录"
                hint="对应配置中的 fuse_strm_mount_dir，例如：/emby/115" persistent-hint density="compact" variant="outlined"
                placeholder="/emby/115"></v-text-field>
            </v-col>
          </v-row>

          <v-row>
            <v-col cols="12">
              <v-text-field v-model="configGeneratorDialog.moviepilotAddress" label="MoviePilot 地址"
                hint="对应配置中的 moviepilot_address" persistent-hint density="compact" variant="outlined"
                placeholder="http://localhost:3000"></v-text-field>
            </v-col>
          </v-row>


          <v-divider class="my-4"></v-divider>

          <div class="mb-2">
            <div class="text-body-2 mb-2"><strong>生成的配置：</strong></div>
            <div v-if="configGeneratorDialog.loading" class="d-flex flex-column align-center py-4">
              <v-progress-circular indeterminate color="primary" class="mb-3"></v-progress-circular>
              <div class="text-body-2 text-grey">正在生成配置...</div>
            </div>
            <v-textarea v-else v-model="configGeneratorDialog.generatedConfig" label="配置代码"
              hint="复制此配置到 emby2Alist 的 mediaPathMapping 数组中" persistent-hint rows="8" variant="outlined"
              density="compact" readonly class="font-monospace"
              style="font-family: 'Courier New', monospace;"></v-textarea>
          </div>

          <v-alert type="warning" variant="tonal" density="compact" class="mt-3" icon="mdi-alert">
            <div class="text-body-2 mb-1"><strong>配置说明：</strong></div>
            <div class="text-caption">
              <div>• 规则：将 <code>/emby/115</code> 替换为插件重定向地址（保留后续路径）</div>
            </div>
          </v-alert>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions class="px-3 py-2">
          <v-btn color="grey" variant="text" @click="closeConfigGeneratorDialog" size="small"
            prepend-icon="mdi-close">关闭</v-btn>
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="text" @click="copyGeneratedConfig" size="small"
            prepend-icon="mdi-content-copy"
            :disabled="configGeneratorDialog.loading || !configGeneratorDialog.generatedConfig">
            复制配置
          </v-btn>
          <v-btn color="primary" variant="text" @click="generateConfig" size="small" prepend-icon="mdi-refresh"
            :disabled="configGeneratorDialog.loading" :loading="configGeneratorDialog.loading">
            重新生成
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 生活事件故障检查对话框 -->
    <v-dialog v-model="lifeEventCheckDialog.show" max-width="1000" scrollable>
      <v-card>
        <v-card-title class="d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon icon="mdi-bug-check" class="mr-2" color="primary" size="small" />
          <span>115生活事件故障检查</span>
        </v-card-title>
        <v-card-text class="px-3 py-3">
          <v-alert v-if="lifeEventCheckDialog.error" type="error" density="compact" class="mb-3" variant="tonal"
            closable>
            {{ lifeEventCheckDialog.error }}
          </v-alert>
          <div v-if="lifeEventCheckDialog.loading" class="d-flex flex-column align-center py-3">
            <v-progress-circular indeterminate color="primary" size="48" class="mb-3"></v-progress-circular>
            <div class="text-body-2 text-grey">正在检查...</div>
          </div>
          <div v-else-if="lifeEventCheckDialog.result">
            <v-alert :type="lifeEventCheckDialog.result.data?.success ? 'success' : 'warning'" density="compact"
              class="mb-3" variant="tonal">
              <div class="text-subtitle-2 mb-1">
                <v-icon :icon="lifeEventCheckDialog.result.data?.success ? 'mdi-check-circle' : 'mdi-alert-circle'"
                  class="mr-1" size="small"></v-icon>
                {{ lifeEventCheckDialog.result.msg }}
              </div>
              <div v-if="lifeEventCheckDialog.result.data?.error_messages?.length" class="mt-2">
                <div class="text-caption mb-1"><strong>发现的问题：</strong></div>
                <div v-for="(msg, idx) in lifeEventCheckDialog.result.data.error_messages" :key="idx"
                  class="text-caption d-flex align-start mb-1">
                  <v-icon icon="mdi-alert" size="x-small" class="mr-1 mt-1" color="warning"></v-icon>
                  <span>{{ msg }}</span>
                </div>
              </div>
            </v-alert>

            <div class="mb-3">
              <div class="d-flex align-center mb-2">
                <v-icon icon="mdi-information" size="small" class="mr-2" color="info"></v-icon>
                <strong class="text-body-2">检查结果摘要</strong>
                <v-spacer></v-spacer>
                <v-btn size="small" variant="outlined" prepend-icon="mdi-content-copy" @click="copyDebugInfo">
                  复制调试信息
                </v-btn>
              </div>
              <v-card variant="outlined" class="pa-3">
                <v-row dense>
                  <v-col cols="12" md="6">
                    <div class="d-flex align-center mb-2">
                      <v-icon
                        :icon="lifeEventCheckDialog.result.data?.summary?.plugin_enabled ? 'mdi-check-circle' : 'mdi-close-circle'"
                        :color="lifeEventCheckDialog.result.data?.summary?.plugin_enabled ? 'success' : 'error'"
                        size="small" class="mr-2"></v-icon>
                      <span class="text-caption">插件启用:
                        <strong>{{ lifeEventCheckDialog.result.data?.summary?.plugin_enabled ? '是' : '否' }}</strong>
                      </span>
                    </div>
                  </v-col>
                  <v-col cols="12" md="6">
                    <div class="d-flex align-center mb-2">
                      <v-icon
                        :icon="lifeEventCheckDialog.result.data?.summary?.client_initialized ? 'mdi-check-circle' : 'mdi-close-circle'"
                        :color="lifeEventCheckDialog.result.data?.summary?.client_initialized ? 'success' : 'error'"
                        size="small" class="mr-2"></v-icon>
                      <span class="text-caption">客户端初始化:
                        <strong>{{ lifeEventCheckDialog.result.data?.summary?.client_initialized ? '是' : '否' }}</strong>
                      </span>
                    </div>
                  </v-col>
                  <v-col cols="12" md="6">
                    <div class="d-flex align-center mb-2">
                      <v-icon
                        :icon="lifeEventCheckDialog.result.data?.summary?.monitorlife_initialized ? 'mdi-check-circle' : 'mdi-close-circle'"
                        :color="lifeEventCheckDialog.result.data?.summary?.monitorlife_initialized ? 'success' : 'error'"
                        size="small" class="mr-2"></v-icon>
                      <span class="text-caption">MonitorLife初始化:
                        <strong>{{ lifeEventCheckDialog.result.data?.summary?.monitorlife_initialized ? '是' : '否'
                        }}</strong>
                      </span>
                    </div>
                  </v-col>
                  <v-col cols="12" md="6">
                    <div class="d-flex align-center mb-2">
                      <v-icon
                        :icon="lifeEventCheckDialog.result.data?.summary?.thread_running ? 'mdi-check-circle' : 'mdi-close-circle'"
                        :color="lifeEventCheckDialog.result.data?.summary?.thread_running ? 'success' : 'error'"
                        size="small" class="mr-2"></v-icon>
                      <span class="text-caption">线程运行:
                        <strong>{{ lifeEventCheckDialog.result.data?.summary?.thread_running ? '是' : '否' }}</strong>
                      </span>
                    </div>
                  </v-col>
                  <v-col cols="12">
                    <div class="d-flex align-center mb-2">
                      <v-icon
                        :icon="lifeEventCheckDialog.result.data?.summary?.config_valid ? 'mdi-check-circle' : 'mdi-close-circle'"
                        :color="lifeEventCheckDialog.result.data?.summary?.config_valid ? 'success' : 'error'"
                        size="small" class="mr-2"></v-icon>
                      <span class="text-caption">配置有效:
                        <strong>{{ lifeEventCheckDialog.result.data?.summary?.config_valid ? '是' : '否' }}</strong>
                      </span>
                    </div>
                  </v-col>
                </v-row>
              </v-card>
            </div>

            <div>
              <div class="d-flex align-center mb-2">
                <v-icon icon="mdi-code-tags" size="small" class="mr-2" color="primary"></v-icon>
                <strong class="text-body-2">详细调试信息</strong>
              </div>
              <v-textarea :model-value="lifeEventCheckDialog.result.data?.debug_info || ''" readonly variant="outlined"
                rows="15" auto-grow class="text-caption font-monospace debug-info-textarea"
                style="font-size: 0.75rem; line-height: 1.6; white-space: pre-wrap;" hint="此信息可用于开发者诊断问题，请复制给开发者"
                persistent-hint></v-textarea>
            </div>
          </div>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions class="px-3 py-2">
          <v-btn color="grey" variant="text" @click="closeLifeEventCheckDialog" size="small"
            prepend-icon="mdi-close">关闭</v-btn>
          <v-spacer></v-spacer>
          <v-btn color="primary" variant="text" @click="checkLifeEventStatus" :disabled="lifeEventCheckDialog.loading"
            size="small" prepend-icon="mdi-refresh">
            重新检查
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 一键导入频道配置对话框 -->
    <v-dialog v-model="importDialog.show" max-width="600" persistent>
      <v-card>
        <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-lighten-5">
          <v-icon icon="mdi-import" class="mr-2" color="primary" size="small" />
          <span>一键导入频道配置</span>
        </v-card-title>
        <v-card-text class="py-4">
          <v-alert v-if="importDialog.error" type="error" density="compact" class="mb-3" variant="tonal" closable>
            {{ importDialog.error }}
          </v-alert>
          <p class="text-caption mb-2 text-grey-darken-1">
            请在此处粘贴JSON格式的频道列表。格式应为：<br>
            <code>[{"name":"名称1", "id":"id1"}, {"name":"名称2", "id":"id2"}]</code>
          </p>
          <v-textarea v-model="importDialog.jsonText" label="频道配置JSON" variant="outlined" rows="8" auto-grow
            hide-details="auto" placeholder='[{"name":"Lsp115","id":"Lsp115"}]'></v-textarea>
        </v-card-text>
        <v-divider></v-divider>
        <v-card-actions class="px-3 py-2">
          <v-spacer></v-spacer>
          <v-btn color="grey" variant="text" @click="closeImportDialog" size="small">
            取消
          </v-btn>
          <v-btn color="primary" variant="text" @click="handleConfirmImport" size="small">
            确认导入
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue';
import { ensureSentryInitialized } from '../utils/init-sentry.js';

const props = defineProps({
  api: {
    type: [Object, Function],
    required: true
  },
  initialConfig: {
    type: Object,
    default: () => ({})
  }
});

const emit = defineEmits(['save', 'close', 'switch']);

// 定义插件ID常量，修复pluginId未定义错误
const PLUGIN_ID = "P115StrmHelper";

// 状态变量
const loading = ref(true);
const saveLoading = ref(false);
const syncLoading = ref(false);
const clearIdPathCacheLoading = ref(false);
const clearIncrementSkipCacheLoading = ref(false);
// 主分类标签
const mainCategory = ref('category-strm');
// 子标签页
const strmSubTab = ref('tab-transfer');
const panSubTab = ref('tab-pan-transfer');
const otherSubTab = ref('tab-sync-del');
const systemSubTab = ref('tab-cache-config');
const mediaservers = ref([]);
// 过滤出 Emby 类型的媒体服务器（用于同步删除功能）
const embyMediaservers = computed(() => {
  return mediaservers.value.filter(server => server.type === 'emby');
});
const isCookieVisible = ref(false);
const isAliTokenVisible = ref(false);
const isTransferModuleEnhancementLocked = ref(true);
// 基础设置折叠状态
const basicConfigExpanded = ref((() => {
  try {
    const saved = localStorage.getItem('p115strmhelper_basic_config_expanded');
    return saved !== null ? JSON.parse(saved) : ['basic-config'];
  } catch (e) {
    return ['basic-config'];
  }
})());
const config = reactive({
  language: "zh_CN",
  enabled: false,
  notify: false,
  strm_url_format: 'pickcode',
  link_redirect_mode: 'cookie',
  cookies: '',
  aliyundrive_token: '',
  password: '',
  moviepilot_address: '',
  user_rmt_mediaext: 'mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v',
  user_download_mediaext: 'srt,ssa,ass',
  transfer_monitor_enabled: false,
  transfer_monitor_scrape_metadata_enabled: false,
  transfer_monitor_scrape_metadata_exclude_paths: '',
  transfer_monitor_paths: '',
  transfer_mp_mediaserver_paths: '',
  transfer_monitor_media_server_refresh_enabled: false,
  transfer_monitor_mediaservers: [],
  timing_full_sync_strm: false,
  full_sync_overwrite_mode: "never",
  full_sync_remove_unless_strm: false,
  full_sync_remove_unless_dir: false,
  full_sync_remove_unless_file: false,
  full_sync_media_server_refresh_enabled: false,
  full_sync_mediaservers: [],
  full_sync_auto_download_mediainfo_enabled: false,
  full_sync_strm_log: true,
  full_sync_batch_num: 5000,
  full_sync_process_num: 128,
  cron_full_sync_strm: '0 */7 * * *',
  full_sync_strm_paths: '',
  full_sync_iter_function: 'iter_files_with_path_skim',
  full_sync_min_file_size: 0,
  full_sync_process_rust: false,
  full_sync_remove_unless_max_threshold: 10,
  full_sync_remove_unless_stable_threshold: 5,
  increment_sync_strm_enabled: false,
  increment_sync_auto_download_mediainfo_enabled: false,
  increment_sync_cron: "0 * * * *",
  increment_sync_strm_paths: '',
  increment_sync_mp_mediaserver_paths: '',
  increment_sync_scrape_metadata_enabled: false,
  increment_sync_scrape_metadata_exclude_paths: '',
  increment_sync_media_server_refresh_enabled: false,
  increment_sync_mediaservers: [],
  increment_sync_min_file_size: 0,
  monitor_life_enabled: false,
  monitor_life_auto_download_mediainfo_enabled: false,
  monitor_life_paths: '',
  monitor_life_mp_mediaserver_paths: '',
  monitor_life_media_server_refresh_enabled: false,
  monitor_life_mediaservers: [],
  monitor_life_event_modes: [],
  monitor_life_scrape_metadata_enabled: false,
  monitor_life_scrape_metadata_exclude_paths: '',
  monitor_life_remove_mp_history: false,
  monitor_life_remove_mp_source: false,
  monitor_life_min_file_size: 0,
  monitor_life_event_wait_time: 0,
  share_strm_config: [],
  share_strm_mediaservers: [],
  share_strm_mp_mediaserver_paths: '',
  api_strm_config: [],
  api_strm_mediaservers: [],
  api_strm_mp_mediaserver_paths: '',
  api_strm_scrape_metadata_enabled: false,
  api_strm_media_server_refresh_enabled: false,
  clear_recyclebin_enabled: false,
  clear_receive_path_enabled: false,
  cron_clear: '0 */7 * * *',
  pan_transfer_enabled: false,
  pan_transfer_paths: '',
  pan_transfer_unrecognized_path: '',
  share_recieve_paths: [],
  offline_download_paths: [],
  directory_upload_enabled: false,
  directory_upload_mode: 'compatibility',
  directory_upload_uploadext: 'mp4,mkv,ts,iso,rmvb,avi,mov,mpeg,mpg,wmv,3gp,asf,m4v,flv,m2ts,tp,f4v',
  directory_upload_copyext: 'srt,ssa,ass',
  directory_upload_path: [],
  nullbr_app_id: '',
  nullbr_api_key: '',
  tg_search_channels: [],
  same_playback: false,
  error_info_upload: false,
  upload_module_enhancement: false,
  upload_module_skip_slow_upload: false,
  upload_module_notify: true,
  upload_module_wait_time: 300,
  upload_module_wait_timeout: 3600,
  upload_module_skip_upload_wait_size: 0,
  upload_module_force_upload_wait_size: 0,
  upload_module_skip_slow_upload_size: 0,
  upload_open_result_notify: false,
  upload_share_info: true,
  upload_offline_info: true,
  transfer_module_enhancement: false,
  pan_transfer_takeover: false,
  strm_url_template_enabled: false,
  strm_url_template: '',
  strm_url_template_custom: '',
  strm_filename_template_enabled: false,
  strm_filename_template: '',
  strm_filename_template_custom: '',
  strm_generate_blacklist: [],
  mediainfo_download_whitelist: [],
  mediainfo_download_blacklist: [],
  strm_url_encode: false,
  storage_module: 'u115',
  sync_del_enabled: false,
  sync_del_notify: true,
  sync_del_source: false,
  sync_del_p115_library_path: '',
  sync_del_p115_force_delete_files: false,
  sync_del_remove_versions: false,
  sync_del_mediaservers: []
});

// 消息提示
const message = reactive({
  text: '',
  type: 'info'
});

const skipUploadWaitSizeFormatted = computed({
  get() {
    if (!config.upload_module_skip_upload_wait_size || config.upload_module_skip_upload_wait_size <= 0) {
      return '';
    }
    return formatBytes(config.upload_module_skip_upload_wait_size);
  },
  set(newValue) {
    config.upload_module_skip_upload_wait_size = parseSize(newValue);
  },
});

const forceUploadWaitSizeFormatted = computed({
  get() {
    if (!config.upload_module_force_upload_wait_size || config.upload_module_force_upload_wait_size <= 0) {
      return '';
    }
    return formatBytes(config.upload_module_force_upload_wait_size);
  },
  set(newValue) {
    config.upload_module_force_upload_wait_size = parseSize(newValue);
  },
});

const skipSlowUploadSizeFormatted = computed({
  get() {
    if (!config.upload_module_skip_slow_upload_size || config.upload_module_skip_slow_upload_size <= 0) {
      return '';
    }
    return formatBytes(config.upload_module_skip_slow_upload_size);
  },
  set(newValue) {
    config.upload_module_skip_slow_upload_size = parseSize(newValue);
  },
});

const fullSyncMinFileSizeFormatted = computed({
  get() {
    if (!config.full_sync_min_file_size || config.full_sync_min_file_size <= 0) {
      return '';
    }
    return formatBytes(config.full_sync_min_file_size);
  },
  set(newValue) {
    config.full_sync_min_file_size = parseSize(newValue);
  },
});

const incrementSyncMinFileSizeFormatted = computed({
  get() {
    if (!config.increment_sync_min_file_size || config.increment_sync_min_file_size <= 0) {
      return '';
    }
    return formatBytes(config.increment_sync_min_file_size);
  },
  set(newValue) {
    config.increment_sync_min_file_size = parseSize(newValue);
  },
});

const monitorLifeMinFileSizeFormatted = computed({
  get() {
    if (!config.monitor_life_min_file_size || config.monitor_life_min_file_size <= 0) {
      return '';
    }
    return formatBytes(config.monitor_life_min_file_size);
  },
  set(newValue) {
    config.monitor_life_min_file_size = parseSize(newValue);
  },
});

const parseSize = (sizeString) => {
  if (!sizeString || typeof sizeString !== 'string') return 0;
  const regex = /^(\d*\.?\d+)\s*(k|m|g|t)?b?$/i;
  const match = sizeString.trim().match(regex);
  if (!match) return 0;
  const num = parseFloat(match[1]);
  const unit = (match[2] || '').toLowerCase();
  switch (unit) {
    case 't': return Math.round(num * 1024 * 1024 * 1024 * 1024);
    case 'g': return Math.round(num * 1024 * 1024 * 1024);
    case 'm': return Math.round(num * 1024 * 1024);
    case 'k': return Math.round(num * 1024);
    default: return Math.round(num);
  }
};

const formatBytes = (bytes, decimals = 2) => {
  if (!+bytes) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'K', 'M', 'G', 'T'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const formattedNum = parseFloat((bytes / Math.pow(k, i)).toFixed(dm));
  return `${formattedNum} ${sizes[i]}`;
};

// 路径管理
const transferPaths = ref([{ local: '', remote: '' }]);
const transferMpPaths = ref([{ local: '', remote: '' }]);
const fullSyncPaths = ref([{ local: '', remote: '' }]);
const incrementSyncPaths = ref([{ local: '', remote: '' }]);
const incrementSyncMPPaths = ref([{ local: '', remote: '' }]);
const monitorLifePaths = ref([{ local: '', remote: '' }]);
const monitorLifeMpPaths = ref([{ local: '', remote: '' }]);
const apiStrmPaths = ref([{ local: '', remote: '' }]);
const apiStrmMPPaths = ref([{ local: '', remote: '' }]);
const panTransferPaths = ref([{ path: '' }]);
const shareReceivePaths = ref([{ path: '' }]);
const offlineDownloadPaths = ref([{ path: '' }]);
const transferExcludePaths = ref([{ path: '' }]);
const incrementSyncExcludePaths = ref([{ local: '', remote: '' }]);
const monitorLifeExcludePaths = ref([{ path: '' }]);
const directoryUploadPaths = ref([{ src: '', dest_remote: '', dest_local: '', dest_strm: '', delete: false }]);
const syncDelLibraryPaths = ref([{ mediaserver: '', moviepilot: '', p115: '' }]);
const fuseStrmTakeoverRules = ref([{ extensions: '', names: '', paths: '', _use_extensions: false, _use_names: false, _use_paths: false }]);
const fullSyncConfirmDialog = ref(false);
const machineId = ref('');
const tgChannels = ref([{ name: '', id: '' }]);

const addTgChannel = () => {
  tgChannels.value.push({ name: '', id: '' });
};

const removeTgChannel = (index) => {
  tgChannels.value.splice(index, 1);
  if (tgChannels.value.length === 0) {
    tgChannels.value.push({ name: '', id: '' });
  }
};

const importDialog = reactive({
  show: false,
  jsonText: '',
  error: ''
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
  callback: null,
  type: '',
  index: -1,
  fieldKey: null,
  targetConfigKeyForExclusion: null,
  originalPathTypeBackup: '',
  originalIndexBackup: -1
});

// 捐赠对话框
const donateDialog = reactive({
  show: false,
  loading: false,
  error: null,
  donateInfo: null,
  activeTab: 'wechat',
  authorizationStatus: null,
});

// 二维码登录对话框
const qrDialog = reactive({
  show: false,
  loading: false,
  error: null,
  qrcode: '',
  uid: '',
  time: "",
  sign: "",
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

// 阿里云盘二维码登录对话框
const aliQrDialog = reactive({
  show: false,
  loading: false,
  error: null,
  qrcode: '',
  t: '',
  ck: '',
  status: '等待扫码',
  checkIntervalId: null,
});

// 生活事件故障检查对话框
const lifeEventCheckDialog = reactive({
  show: false,
  loading: false,
  error: null,
  result: null,
});

// emby2Alist 配置生成对话框
const configGeneratorDialog = reactive({
  show: false,
  loading: false,
  mountDir: '',
  moviepilotAddress: '',
  generatedConfig: ''
});

// 手动整理对话框
const manualTransferDialog = reactive({
  show: false,
  loading: false,
  path: '',
  result: null, // { type: 'success' | 'error', title: string, message: string }
});

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

watch(() => config.increment_sync_strm_paths, (newVal) => {
  if (!newVal) {
    incrementSyncPaths.value = [{ local: '', remote: '' }];
    return;
  }
  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    incrementSyncPaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });
    if (incrementSyncPaths.value.length === 0) {
      incrementSyncPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析increment_sync_strm_paths出错:', e);
    incrementSyncPaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.increment_sync_mp_mediaserver_paths, (newVal) => {
  if (!newVal) {
    incrementSyncMPPaths.value = [{ local: '', remote: '' }];
    return;
  }
  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    incrementSyncMPPaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });
    if (incrementSyncMPPaths.value.length === 0) {
      incrementSyncMPPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析increment_sync_mp_mediaserver_paths出错:', e);
    incrementSyncMPPaths.value = [{ local: '', remote: '' }];
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

watch(() => config.api_strm_config, (newVal) => {
  if (!newVal || !Array.isArray(newVal)) {
    apiStrmPaths.value = [{ local: '', remote: '' }];
    return;
  }
  try {
    apiStrmPaths.value = newVal.map(item => ({
      local: item.local_path || '',
      remote: item.pan_path || ''
    }));
    if (apiStrmPaths.value.length === 0) {
      apiStrmPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析api_strm_config出错:', e);
    apiStrmPaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.sync_del_p115_library_path, (newVal) => {
  if (!newVal) {
    syncDelLibraryPaths.value = [{ mediaserver: '', moviepilot: '', p115: '' }];
    return;
  }
  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    syncDelLibraryPaths.value = paths.map(path => {
      const parts = path.split('#');
      return {
        mediaserver: parts[0] || '',
        moviepilot: parts[1] || '',
        p115: parts[2] || ''
      };
    });
    if (syncDelLibraryPaths.value.length === 0) {
      syncDelLibraryPaths.value = [{ mediaserver: '', moviepilot: '', p115: '' }];
    }
  } catch (e) {
    console.error('解析sync_del_p115_library_path出错:', e);
    syncDelLibraryPaths.value = [{ mediaserver: '', moviepilot: '', p115: '' }];
  }
}, { immediate: true });

watch(() => config.api_strm_mp_mediaserver_paths, (newVal) => {
  if (!newVal) {
    apiStrmMPPaths.value = [{ local: '', remote: '' }];
    return;
  }
  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    apiStrmMPPaths.value = paths.map(path => {
      const parts = path.split('#');
      return { local: parts[0] || '', remote: parts[1] || '' };
    });
    if (apiStrmMPPaths.value.length === 0) {
      apiStrmMPPaths.value = [{ local: '', remote: '' }];
    }
  } catch (e) {
    console.error('解析api_strm_mp_mediaserver_paths出错:', e);
    apiStrmMPPaths.value = [{ local: '', remote: '' }];
  }
}, { immediate: true });

watch(() => config.share_recieve_paths, (newVal) => {
  if (!newVal || !Array.isArray(newVal)) {
    shareReceivePaths.value = [{ path: '' }];
    return;
  }
  try {
    shareReceivePaths.value = newVal.map(path => {
      return { path: typeof path === 'string' ? path : '' };
    });
    if (shareReceivePaths.value.length === 0) {
      shareReceivePaths.value = [{ path: '' }];
    }
  } catch (e) {
    console.error('解析share_recieve_paths出错:', e);
    shareReceivePaths.value = [{ path: '' }];
  }
}, { immediate: true });

watch(() => config.offline_download_paths, (newVal) => {
  if (!newVal || !Array.isArray(newVal)) {
    offlineDownloadPaths.value = [{ path: '' }];
    return;
  }
  try {
    offlineDownloadPaths.value = newVal.map(path => {
      return { path: typeof path === 'string' ? path : '' };
    });
    if (offlineDownloadPaths.value.length === 0) {
      offlineDownloadPaths.value = [{ path: '' }];
    }
  } catch (e) {
    console.error('解析offline_download_paths出错:', e);
    offlineDownloadPaths.value = [{ path: '' }];
  }
}, { immediate: true });

watch(() => config.transfer_monitor_scrape_metadata_exclude_paths, (newVal) => {
  if (typeof newVal !== 'string' || !newVal.trim()) {
    transferExcludePaths.value = [{ path: '' }];
    return;
  }
  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    transferExcludePaths.value = paths.map(p => ({ path: p }));
    if (transferExcludePaths.value.length === 0) {
      transferExcludePaths.value = [{ path: '' }];
    }
  } catch (e) {
    console.error('解析 transfer_monitor_scrape_metadata_exclude_paths 出错:', e);
    transferExcludePaths.value = [{ path: '' }];
  }
}, { immediate: true });

watch(transferExcludePaths, (newVal) => {
  if (!Array.isArray(newVal)) return;
  const pathsString = newVal
    .map(item => item.path?.trim())
    .filter(p => p)
    .join('\n');
  if (config.transfer_monitor_scrape_metadata_exclude_paths !== pathsString) {
    config.transfer_monitor_scrape_metadata_exclude_paths = pathsString;
  }
}, { deep: true });

watch(() => config.increment_sync_scrape_metadata_exclude_paths, (newVal) => {
  if (typeof newVal !== 'string' || !newVal.trim()) {
    incrementSyncExcludePaths.value = [{ path: '' }];
    return;
  }
  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    incrementSyncExcludePaths.value = paths.map(p => ({ path: p }));
    if (incrementSyncExcludePaths.value.length === 0) {
      incrementSyncExcludePaths.value = [{ path: '' }];
    }
  } catch (e) {
    console.error('解析 increment_sync_scrape_metadata_exclude_paths 出错:', e);
    incrementSyncExcludePaths.value = [{ path: '' }];
  }
}, { immediate: true });

watch(incrementSyncExcludePaths, (newVal) => {
  if (!Array.isArray(newVal)) return;
  const pathsString = newVal
    .map(item => item.path?.trim())
    .filter(p => p)
    .join('\n');
  if (config.increment_sync_scrape_metadata_exclude_paths !== pathsString) {
    config.increment_sync_scrape_metadata_exclude_paths = pathsString;
  }
}, { deep: true });

watch(() => config.monitor_life_scrape_metadata_exclude_paths, (newVal) => {
  if (typeof newVal !== 'string' || !newVal.trim()) {
    monitorLifeExcludePaths.value = [{ path: '' }];
    return;
  }
  try {
    const paths = newVal.split('\n').filter(line => line.trim());
    monitorLifeExcludePaths.value = paths.map(p => ({ path: p }));
    if (monitorLifeExcludePaths.value.length === 0) {
      monitorLifeExcludePaths.value = [{ path: '' }];
    }
  } catch (e) {
    console.error('解析 monitor_life_scrape_metadata_exclude_paths 出错:', e);
    monitorLifeExcludePaths.value = [{ path: '' }];
  }
}, { immediate: true });

watch(monitorLifeExcludePaths, (newVal) => {
  if (!Array.isArray(newVal)) return;
  const pathsString = newVal
    .map(item => item.path?.trim())
    .filter(p => p)
    .join('\n');
  if (config.monitor_life_scrape_metadata_exclude_paths !== pathsString) {
    config.monitor_life_scrape_metadata_exclude_paths = pathsString;
  }
}, { deep: true });

// 监听 full_sync_remove_unless_strm 变化，当禁用时自动禁用依赖的配置项
watch(() => config.full_sync_remove_unless_strm, (newVal) => {
  if (!newVal) {
    // 当主配置被禁用时，自动禁用依赖的配置项
    config.full_sync_remove_unless_dir = false;
    config.full_sync_remove_unless_file = false;
  }
});

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

const checkTransferModuleEnhancement = async () => {
  try {
    const result = await props.api.get(`plugin/${PLUGIN_ID}/check_feature?name=transfer_module_enhancement`);
    if (result && result.enabled === true) {
      isTransferModuleEnhancementLocked.value = false;
    } else {
      isTransferModuleEnhancementLocked.value = true;
      config.transfer_module_enhancement = false;
    }
  } catch (err) {
    isTransferModuleEnhancementLocked.value = true;
    config.transfer_module_enhancement = false;
    console.error('检查 "整理模块增强" 功能授权失败:', err);
  }
};

// 加载配置
const loadConfig = async () => {
  try {
    loading.value = true;
    const data = await props.api.get(`plugin/${PLUGIN_ID}/get_config`);
    if (data) {
      Object.assign(config, data);
      directoryUploadPaths.value = (Array.isArray(config.directory_upload_path) && config.directory_upload_path.length > 0)
        ? config.directory_upload_path.map(p => ({ src: p.src ?? '', dest_remote: p.dest_remote ?? '', dest_local: p.dest_local ?? '', dest_strm: p.dest_strm ?? '', delete: !!p.delete }))
        : [{ src: '', dest_remote: '', dest_local: '', dest_strm: '', delete: false }];
      let parsedChannels = [];
      if (config.tg_search_channels) {
        if (Array.isArray(config.tg_search_channels)) {
          parsedChannels = config.tg_search_channels;
        }
        else if (typeof config.tg_search_channels === 'string') {
          try {
            parsedChannels = JSON.parse(config.tg_search_channels);
          } catch (e) {
            console.error('解析旧的TG频道配置字符串失败:', e);
            parsedChannels = [];
          }
        }
      }
      if (Array.isArray(parsedChannels) && parsedChannels.length > 0) {
        tgChannels.value = parsedChannels;
      } else {
        tgChannels.value = [{ name: '', id: '' }];
      }
      if (data.mediaservers) {
        mediaservers.value = data.mediaservers;
      }
      // 加载 FUSE STRM 接管规则
      if (config.fuse_strm_takeover_rules && Array.isArray(config.fuse_strm_takeover_rules) && config.fuse_strm_takeover_rules.length > 0) {
        fuseStrmTakeoverRules.value = config.fuse_strm_takeover_rules.map(rule => {
          const extensions = Array.isArray(rule.extensions) ? rule.extensions : [];
          const names = Array.isArray(rule.names) ? rule.names : [];
          const paths = Array.isArray(rule.paths) ? rule.paths : [];
          return {
            extensions: extensions.join('\n'),
            names: names.join('\n'),
            paths: paths.join('\n'),
            _use_extensions: extensions.length > 0,
            _use_names: names.length > 0,
            _use_paths: paths.length > 0
          };
        });
      } else {
        fuseStrmTakeoverRules.value = [{ extensions: '', names: '', paths: '', _use_extensions: false, _use_names: false, _use_paths: false }];
      }
      // 确保 sync_del_mediaservers 如果是 null，转换为空数组以匹配前端显示
      if (config.sync_del_mediaservers === null || config.sync_del_mediaservers === undefined) {
        config.sync_del_mediaservers = [];
      }
      const p115LocalPaths = new Set();
      if (config.transfer_monitor_paths) {
        config.transfer_monitor_paths.split('\n')
          .map(p => p.split('#')[0]?.trim()).filter(p => p).forEach(p => p115LocalPaths.add(p));
      }
      if (config.full_sync_strm_paths) {
        config.full_sync_strm_paths.split('\n')
          .map(p => p.split('#')[0]?.trim()).filter(p => p).forEach(p => p115LocalPaths.add(p));
      }
      if (config.monitor_life_paths) {
        config.monitor_life_paths.split('\n')
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
    config.transfer_monitor_paths = generatePathsConfig(transferPaths.value, 'transfer');
    config.transfer_mp_mediaserver_paths = generatePathsConfig(transferMpPaths.value, 'mp');
    config.full_sync_strm_paths = generatePathsConfig(fullSyncPaths.value, 'fullSync');
    config.increment_sync_strm_paths = generatePathsConfig(incrementSyncPaths.value, 'incrementSync');
    config.increment_sync_mp_mediaserver_paths = generatePathsConfig(incrementSyncMPPaths.value, 'increment-mp');
    config.monitor_life_paths = generatePathsConfig(monitorLifePaths.value, 'monitorLife');
    config.monitor_life_mp_mediaserver_paths = generatePathsConfig(monitorLifeMpPaths.value, 'monitorLifeMp');
    config.api_strm_config = apiStrmPaths.value
      .filter(p => p.local?.trim() && p.remote?.trim())
      .map(p => ({ local_path: p.local.trim(), pan_path: p.remote.trim() }));
    config.api_strm_mp_mediaserver_paths = generatePathsConfig(apiStrmMPPaths.value, 'apiStrm-mp');
    config.sync_del_p115_library_path = syncDelLibraryPaths.value
      .filter(p => p.mediaserver?.trim() || p.moviepilot?.trim() || p.p115?.trim())
      .map(p => `${p.mediaserver || ''}#${p.moviepilot || ''}#${p.p115 || ''}`)
      .join('\n');
    // 处理 sync_del_mediaservers：如果数组为空，转换为 null 以匹配后端的 Optional[List[str]]
    if (Array.isArray(config.sync_del_mediaservers) && config.sync_del_mediaservers.length === 0) {
      config.sync_del_mediaservers = null;
    }
    config.pan_transfer_paths = generatePathsConfig(panTransferPaths.value, 'panTransfer');
    config.share_recieve_paths = shareReceivePaths.value.filter(p => p.path?.trim()).map(p => p.path);
    config.offline_download_paths = offlineDownloadPaths.value.filter(p => p.path?.trim()).map(p => p.path);
    config.directory_upload_path = directoryUploadPaths.value.filter(p => p.src?.trim() || p.dest_remote?.trim() || p.dest_local?.trim() || p.dest_strm?.trim());
    const validChannels = tgChannels.value.filter(
      c => c.name && c.name.trim() !== '' && c.id && c.id.trim() !== ''
    );
    config.tg_search_channels = validChannels;
    // 保存 FUSE STRM 接管规则
    config.fuse_strm_takeover_rules = fuseStrmTakeoverRules.value
      .map(rule => {
        const extensions = typeof rule.extensions === 'string'
          ? rule.extensions.split('\n').map(ext => ext.trim()).filter(ext => ext)
          : (rule.extensions || []);
        const names = typeof rule.names === 'string'
          ? rule.names.split('\n').map(name => name.trim()).filter(name => name)
          : (rule.names || []);
        const paths = typeof rule.paths === 'string'
          ? rule.paths.split('\n').map(path => path.trim()).filter(path => path)
          : (rule.paths || []);
        return { extensions, names, paths };
      })
      .filter(rule => rule.extensions.length > 0 || rule.names.length > 0 || rule.paths.length > 0);
    emit('save', JSON.parse(JSON.stringify(config)));
    message.text = '配置已发送保存请求，请稍候...';
    message.type = 'info';
  } catch (err) {
    console.error('发送保存事件时出错:', err);
    message.text = `发送保存请求时出错: ${err.message || '未知错误'}`;
    message.type = 'error';
  } finally {
    saveLoading.value = false;
    setTimeout(() => {
      if (message.type === 'info' || message.type === 'error') {
        message.text = '';
      }
    }, 5000);
  }
};

const getMachineId = async () => {
  machineId.value = '正在获取...';
  try {
    const result = await props.api.get(`plugin/${PLUGIN_ID}/get_machine_id`);
    if (result && result.machine_id) {
      machineId.value = result.machine_id;
      message.text = '设备ID获取成功！';
      message.type = 'success';
    } else {
      throw new Error(result?.msg || '未能获取设备ID');
    }
  } catch (err) {
    machineId.value = '获取失败，请重试';
    message.text = `获取设备ID失败: ${err.message || '未知错误'}`;
    message.type = 'error';
  }
  setTimeout(() => {
    if (message.type === 'success' || message.type === 'info') {
      message.text = '';
    }
  }, 3000);
};

// 格式化授权过期时间（UTC转本地时间）
const formatAuthorizationExpiration = (utcTimeStr) => {
  if (!utcTimeStr) return '未知';
  try {
    const date = new Date(utcTimeStr);
    return date.toLocaleString('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    });
  } catch (err) {
    console.error('格式化时间失败:', err);
    return utcTimeStr;
  }
};

// 打开捐赠对话框
const openDonateDialog = async () => {
  donateDialog.show = true;
  donateDialog.loading = true;
  donateDialog.error = null;
  donateDialog.donateInfo = null;
  donateDialog.authorizationStatus = null;

  try {
    // 并行获取捐赠信息和授权状态
    const [donateResult, authResult] = await Promise.all([
      props.api.get(`plugin/${PLUGIN_ID}/get_donate_info`),
      props.api.get(`plugin/${PLUGIN_ID}/get_authorization_status`)
    ]);

    // 处理捐赠信息
    if (donateResult && donateResult.code === 0 && donateResult.data) {
      donateDialog.donateInfo = donateResult.data;
      // 设置默认激活的tab（默认优先微信）
      if (donateResult.data.wechat?.enabled) {
        donateDialog.activeTab = 'wechat';
      } else if (donateResult.data.alipay?.enabled) {
        donateDialog.activeTab = 'alipay';
      }
    } else {
      throw new Error(donateResult?.msg || '获取捐赠信息失败');
    }

    // 处理授权状态
    if (authResult && authResult.code === 0 && authResult.data) {
      donateDialog.authorizationStatus = authResult.data;
    }
  } catch (err) {
    donateDialog.error = `加载捐赠信息失败: ${err.message || '未知错误'}`;
    console.error('获取捐赠信息失败:', err);
  } finally {
    donateDialog.loading = false;
  }
};

// 关闭捐赠对话框
const closeDonateDialog = () => {
  donateDialog.show = false;
  donateDialog.error = null;
  donateDialog.donateInfo = null;
  donateDialog.authorizationStatus = null;
};

// 复制文本到剪贴板
const copyToClipboard = async (text, successMsg = '已复制到剪贴板') => {
  try {
    await navigator.clipboard.writeText(text);
    message.text = successMsg;
    message.type = 'success';
  } catch (err) {
    console.error('复制失败:', err);
    message.text = '复制失败，请手动复制';
    message.type = 'error';
  }
  setTimeout(() => {
    if (message.type === 'success' || message.type === 'warning' || message.type === 'error') {
      message.text = '';
    }
  }, 3000);
};

const handleConfirmFullSync = async () => {
  fullSyncConfirmDialog.value = false;
  await triggerFullSync();
};

const triggerFullSync = async () => {
  syncLoading.value = true;
  message.text = '';
  try {
    if (!config.enabled) throw new Error('插件未启用，请先启用插件');
    if (!config.cookies || config.cookies.trim() === '') throw new Error('请先设置115 Cookie');
    config.full_sync_strm_paths = generatePathsConfig(fullSyncPaths.value, 'fullSync');
    if (!config.full_sync_strm_paths) throw new Error('请先配置全量同步路径');

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

// 清理文件路径ID缓存
const clearIdPathCache = async () => {
  clearIdPathCacheLoading.value = true;
  message.text = '';
  try {
    const result = await props.api.post(`plugin/${PLUGIN_ID}/clear_id_path_cache`);
    if (result && result.code === 0) {
      message.text = result.msg || '文件路径ID缓存清理成功';
      message.type = 'success';
    } else {
      throw new Error(result?.msg || '文件路径ID缓存清理失败');
    }
  } catch (err) {
    message.text = `文件路径ID缓存清理失败: ${err.message || '未知错误'}`;
    message.type = 'error';
    console.error('文件路径ID缓存清理失败:', err);
  } finally {
    clearIdPathCacheLoading.value = false;
    setTimeout(() => {
      if (message.type === 'success' || message.type === 'error') {
        message.text = '';
      }
    }, 3000);
  }
};

// 清理增量同步跳过路径缓存
const clearIncrementSkipCache = async () => {
  clearIncrementSkipCacheLoading.value = true;
  message.text = '';
  try {
    const result = await props.api.post(`plugin/${PLUGIN_ID}/clear_increment_skip_cache`);
    if (result && result.code === 0) {
      message.text = result.msg || '增量同步跳过路径缓存清理成功';
      message.type = 'success';
    } else {
      throw new Error(result?.msg || '增量同步跳过路径缓存清理失败');
    }
  } catch (err) {
    message.text = `增量同步跳过路径缓存清理失败: ${err.message || '未知错误'}`;
    message.type = 'error';
    console.error('增量同步跳过路径缓存清理失败:', err);
  } finally {
    clearIncrementSkipCacheLoading.value = false;
    setTimeout(() => {
      if (message.type === 'success' || message.type === 'error') {
        message.text = '';
      }
    }, 3000);
  }
};

const addPath = (type) => {
  switch (type) {
    case 'transfer': transferPaths.value.push({ local: '', remote: '' }); break;
    case 'mp': transferMpPaths.value.push({ local: '', remote: '' }); break;
    case 'fullSync': fullSyncPaths.value.push({ local: '', remote: '' }); break;
    case 'incrementSync': incrementSyncPaths.value.push({ local: '', remote: '' }); break;
    case 'increment-mp': incrementSyncMPPaths.value.push({ local: '', remote: '' }); break;
    case 'monitorLife': monitorLifePaths.value.push({ local: '', remote: '' }); break;
    case 'monitorLifeMp': monitorLifeMpPaths.value.push({ local: '', remote: '' }); break;
    case 'apiStrm': apiStrmPaths.value.push({ local: '', remote: '' }); break;
    case 'apiStrm-mp': apiStrmMPPaths.value.push({ local: '', remote: '' }); break;
    case 'syncDelLibrary': syncDelLibraryPaths.value.push({ mediaserver: '', moviepilot: '', p115: '' }); break;
    case 'directoryUpload': directoryUploadPaths.value.push({ src: '', dest_remote: '', dest_local: '', dest_strm: '', delete: false }); break;
    case 'fuseStrmTakeover': fuseStrmTakeoverRules.value.push({ extensions: '', names: '', paths: '', _use_extensions: false, _use_names: false, _use_paths: false }); break;
  }
};
const removePath = (index, type) => {
  switch (type) {
    case 'transfer':
      transferPaths.value.splice(index, 1);
      if (transferPaths.value.length === 0) transferPaths.value = [{ local: '', remote: '' }];
      break;
    case 'mp':
      transferMpPaths.value.splice(index, 1);
      if (transferMpPaths.value.length === 0) transferMpPaths.value = [{ local: '', remote: '' }];
      break;
    case 'fullSync':
      fullSyncPaths.value.splice(index, 1);
      if (fullSyncPaths.value.length === 0) fullSyncPaths.value = [{ local: '', remote: '' }];
      break;
    case 'incrementSync':
      incrementSyncPaths.value.splice(index, 1);
      if (incrementSyncPaths.value.length === 0) incrementSyncPaths.value = [{ local: '', remote: '' }];
      break;
    case 'increment-mp':
      incrementSyncMPPaths.value.splice(index, 1);
      if (incrementSyncMPPaths.value.length === 0) incrementSyncMPPaths.value = [{ local: '', remote: '' }];
      break;
    case 'monitorLife':
      monitorLifePaths.value.splice(index, 1);
      if (monitorLifePaths.value.length === 0) monitorLifePaths.value = [{ local: '', remote: '' }];
      break;
    case 'monitorLifeMp':
      monitorLifeMpPaths.value.splice(index, 1);
      if (monitorLifeMpPaths.value.length === 0) monitorLifeMpPaths.value = [{ local: '', remote: '' }];
      break;
    case 'apiStrm':
      apiStrmPaths.value.splice(index, 1);
      if (apiStrmPaths.value.length === 0) apiStrmPaths.value = [{ local: '', remote: '' }];
      break;
    case 'apiStrm-mp':
      apiStrmMPPaths.value.splice(index, 1);
      if (apiStrmMPPaths.value.length === 0) apiStrmMPPaths.value = [{ local: '', remote: '' }];
      break;
    case 'syncDelLibrary':
      syncDelLibraryPaths.value.splice(index, 1);
      if (syncDelLibraryPaths.value.length === 0) syncDelLibraryPaths.value = [{ mediaserver: '', moviepilot: '', p115: '' }];
      break;
    case 'directoryUpload':
      directoryUploadPaths.value.splice(index, 1);
      if (directoryUploadPaths.value.length === 0) directoryUploadPaths.value = [{ src: '', dest_remote: '', dest_local: '', dest_strm: '', delete: false }];
      break;
    case 'fuseStrmTakeover':
      fuseStrmTakeoverRules.value.splice(index, 1);
      if (fuseStrmTakeoverRules.value.length === 0) fuseStrmTakeoverRules.value = [{ extensions: '', names: '', paths: '', _use_extensions: false, _use_names: false, _use_paths: false }];
      break;
  }
};
const addPanTransferPath = () => { panTransferPaths.value.push({ path: '' }); };
const removePanTransferPath = (index) => {
  panTransferPaths.value.splice(index, 1);
  if (panTransferPaths.value.length === 0) panTransferPaths.value = [{ path: '' }];
};

const manualTransfer = (index) => {
  if (index < 0 || index >= panTransferPaths.value.length) {
    message.text = '路径项不存在';
    message.type = 'error';
    return;
  }

  const pathItem = panTransferPaths.value[index];
  if (!pathItem) {
    message.text = '路径项不存在';
    message.type = 'error';
    return;
  }

  const path = pathItem.path;
  if (!path || typeof path !== 'string' || !path.trim()) {
    message.text = '请先配置网盘路径';
    message.type = 'warning';
    return;
  }

  Object.assign(manualTransferDialog, {
    path: path.trim(),
    loading: false,
    result: null,
    show: true
  });
};

const confirmManualTransfer = async () => {
  if (!manualTransferDialog.path) {
    return;
  }

  manualTransferDialog.loading = true;
  manualTransferDialog.result = null;

  try {
    const result = await props.api.post(`plugin/${PLUGIN_ID}/manual_transfer`, {
      path: manualTransferDialog.path
    });

    if (result.code === 0) {
      manualTransferDialog.result = {
        type: 'success',
        title: '整理任务已启动',
        message: '整理任务已在后台启动，正在执行中。您可以在日志中查看详细进度。'
      };
    } else {
      manualTransferDialog.result = {
        type: 'error',
        title: '启动失败',
        message: result.msg || '启动整理任务失败，请检查配置和网络连接。'
      };
    }
  } catch (error) {
    manualTransferDialog.result = {
      type: 'error',
      title: '启动失败',
      message: `启动整理任务时发生错误：${error.message || error}`
    };
  } finally {
    manualTransferDialog.loading = false;
  }
};

const closeManualTransferDialog = () => {
  Object.assign(manualTransferDialog, {
    show: false,
    path: '',
    loading: false,
    result: null
  });
};

const addShareReceivePath = () => { shareReceivePaths.value.push({ path: '' }); };
const removeShareReceivePath = (index) => {
  shareReceivePaths.value.splice(index, 1);
  if (shareReceivePaths.value.length === 0) shareReceivePaths.value = [{ path: '' }];
};

const addOfflineDownloadPath = () => { offlineDownloadPaths.value.push({ path: '' }); };
const removeOfflineDownloadPath = (index) => {
  offlineDownloadPaths.value.splice(index, 1);
  if (offlineDownloadPaths.value.length === 0) offlineDownloadPaths.value = [{ path: '' }];
};

const openImportDialog = () => {
  importDialog.jsonText = '';
  importDialog.error = '';
  importDialog.show = true;
};
const closeImportDialog = () => {
  importDialog.show = false;
};
const handleConfirmImport = () => {
  importDialog.error = '';
  if (!importDialog.jsonText || !importDialog.jsonText.trim()) {
    importDialog.error = '输入内容不能为空。';
    return;
  }
  try {
    const parsedData = JSON.parse(importDialog.jsonText);
    if (!Array.isArray(parsedData)) throw new Error("数据必须是一个数组。");
    const isValidStructure = parsedData.every(
      item => typeof item === 'object' && item !== null && 'name' in item && 'id' in item
    );
    if (!isValidStructure) throw new Error("数组中的每个元素都必须是包含 'name' 和 'id' 键的对象。");
    tgChannels.value = parsedData.length > 0 ? parsedData : [{ name: '', id: '' }];
    message.text = '频道配置导入成功！';
    message.type = 'success';
    closeImportDialog();
  } catch (e) {
    importDialog.error = `导入失败: ${e.message}`;
    console.error("频道导入解析失败:", e);
  }
};

const openDirSelector = (index, locationType, pathType, fieldKey = null) => {
  dirDialog.show = true;
  dirDialog.isLocal = locationType === 'local';
  dirDialog.loading = false;
  dirDialog.error = null;
  dirDialog.items = [];
  dirDialog.index = index;
  dirDialog.type = pathType;
  dirDialog.fieldKey = fieldKey;
  dirDialog.targetConfigKeyForExclusion = null;
  dirDialog.originalPathTypeBackup = '';
  dirDialog.originalIndexBackup = -1;

  // 设置初始路径
  if (pathType === 'syncDelLibrary' && index >= 0 && syncDelLibraryPaths.value[index] && fieldKey) {
    const currentPath = syncDelLibraryPaths.value[index][fieldKey] || '/';
    dirDialog.currentPath = currentPath;
  } else {
    dirDialog.currentPath = '/';
  }

  loadDirContent();
};

const loadDirContent = async () => {
  dirDialog.loading = true;
  dirDialog.error = null;
  dirDialog.items = [];
  try {
    if (dirDialog.isLocal) {
      try {
        const response = await props.api.post('storage/list', { path: dirDialog.currentPath || '/', type: 'share', flag: 'ROOT' });
        if (response && Array.isArray(response)) {
          dirDialog.items = response
            .filter(item => item.type === 'dir')
            .map(item => ({ name: item.name, path: item.path, is_dir: true }))
            .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
        } else {
          throw new Error('浏览目录失败：无效响应');
        }
      } catch (error) {
        console.error('浏览本地目录失败:', error);
        dirDialog.error = `浏览本地目录失败: ${error.message || '未知错误'}`;
        dirDialog.items = [];
      }
    } else {
      if (!config.cookies || config.cookies.trim() === '') {
        throw new Error('请先设置115 Cookie才能浏览网盘目录');
      }
      const result = await props.api.get(`plugin/${PLUGIN_ID}/browse_dir?path=${encodeURIComponent(dirDialog.currentPath)}&is_local=${dirDialog.isLocal}`);

      if (result && result.code === 0 && result.data) {
        dirDialog.items = result.data.items
          .filter(item => item.is_dir)
          .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' }));
        dirDialog.currentPath = result.data.path || dirDialog.currentPath;
      } else {
        throw new Error(result?.msg || '获取网盘目录内容失败');
      }
    }
  } catch (error) {
    console.error('加载目录内容失败:', error);
    dirDialog.error = error.message || '获取目录内容失败';
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
  if (!dirDialog.isLocal) {
    if (path === '/') return;
    let current = path.replace(/\\/g, '/');
    if (current.length > 1 && current.endsWith('/')) current = current.slice(0, -1);
    const parent = current.substring(0, current.lastIndexOf('/'));
    dirDialog.currentPath = parent === '' ? '/' : parent;
    loadDirContent();
    return;
  }
  if (path === '/' || path === 'C:\\' || path === 'C:/') return;
  const normalizedPath = path.replace(/\\/g, '/');
  const parts = normalizedPath.split('/').filter(Boolean);
  if (parts.length === 0) {
    dirDialog.currentPath = '/';
  } else if (parts.length === 1 && normalizedPath.includes(':')) {
    dirDialog.currentPath = parts[0] + ':/';
  } else {
    parts.pop();
    dirDialog.currentPath = parts.length === 0 ? '/' : (normalizedPath.startsWith('/') ? '/' : '') + parts.join('/') + '/';
  }
  loadDirContent();
};
const confirmDirSelection = () => {
  if (!dirDialog.currentPath) return;
  let processedPath = dirDialog.currentPath;
  if (processedPath !== '/' && !(/^[a-zA-Z]:[\\\/]$/.test(processedPath)) && (processedPath.endsWith('/') || processedPath.endsWith('\\\\'))) {
    processedPath = processedPath.slice(0, -1);
  }
  if (dirDialog.type === 'excludePath' && dirDialog.targetConfigKeyForExclusion) {
    const targetKey = dirDialog.targetConfigKeyForExclusion;
    let targetArrayRef;
    if (targetKey === 'transfer_monitor_scrape_metadata_exclude_paths') targetArrayRef = transferExcludePaths;
    else if (targetKey === 'monitor_life_scrape_metadata_exclude_paths') targetArrayRef = monitorLifeExcludePaths;
    else if (targetKey === 'increment_sync_scrape_metadata_exclude_paths') targetArrayRef = incrementSyncExcludePaths;
    if (targetArrayRef) {
      if (targetArrayRef.value.length === 1 && !targetArrayRef.value[0].path) {
        targetArrayRef.value[0] = { path: processedPath };
      } else {
        if (!targetArrayRef.value.some(item => item.path === processedPath)) {
          targetArrayRef.value.push({ path: processedPath });
        } else {
          message.text = '该排除路径已存在。';
          message.type = 'warning';
          setTimeout(() => { message.text = ''; }, 3000);
        }
      }
    }
    dirDialog.type = dirDialog.originalPathTypeBackup;
    dirDialog.index = dirDialog.originalIndexBackup;
    dirDialog.targetConfigKeyForExclusion = null;
    dirDialog.originalPathTypeBackup = '';
    dirDialog.originalIndexBackup = -1;
  }
  else if (dirDialog.index >= 0 && dirDialog.type !== 'excludePath') {
    switch (dirDialog.type) {
      case 'transfer': dirDialog.isLocal ? transferPaths.value[dirDialog.index].local = processedPath : transferPaths.value[dirDialog.index].remote = processedPath; break;
      case 'fullSync': dirDialog.isLocal ? fullSyncPaths.value[dirDialog.index].local = processedPath : fullSyncPaths.value[dirDialog.index].remote = processedPath; break;
      case 'incrementSync': dirDialog.isLocal ? incrementSyncPaths.value[dirDialog.index].local = processedPath : incrementSyncPaths.value[dirDialog.index].remote = processedPath; break;
      case 'monitorLife': dirDialog.isLocal ? monitorLifePaths.value[dirDialog.index].local = processedPath : monitorLifePaths.value[dirDialog.index].remote = processedPath; break;
      case 'apiStrm': dirDialog.isLocal ? apiStrmPaths.value[dirDialog.index].local = processedPath : apiStrmPaths.value[dirDialog.index].remote = processedPath; break;
      case 'panTransfer': panTransferPaths.value[dirDialog.index].path = processedPath; break;
      case 'shareReceive': shareReceivePaths.value[dirDialog.index].path = processedPath; break;
      case 'offlineDownload': offlineDownloadPaths.value[dirDialog.index].path = processedPath; break;
      case 'syncDelLibrary':
        if (dirDialog.index >= 0 && syncDelLibraryPaths.value[dirDialog.index] && dirDialog.fieldKey) {
          syncDelLibraryPaths.value[dirDialog.index][dirDialog.fieldKey] = processedPath;
        }
        break;
      case 'directoryUpload':
        if (dirDialog.fieldKey && directoryUploadPaths.value[dirDialog.index]) directoryUploadPaths.value[dirDialog.index][dirDialog.fieldKey] = processedPath;
        break;
    }
  }
  else if (dirDialog.type === 'panTransferUnrecognized') config.pan_transfer_unrecognized_path = processedPath;
  closeDirDialog();
};
const closeDirDialog = () => {
  dirDialog.show = false;
  dirDialog.items = [];
  dirDialog.error = null;
};

const copyCookieToClipboard = async () => {
  if (!config.cookies) { message.text = 'Cookie为空，无法复制。'; message.type = 'warning'; return; }
  try {
    await navigator.clipboard.writeText(config.cookies);
    message.text = 'Cookie已复制到剪贴板！';
    message.type = 'success';
  } catch (err) {
    console.error('复制Cookie失败:', err);
    message.text = '复制Cookie失败。请检查浏览器权限或确保通过HTTPS访问，或尝试手动复制。';
    message.type = 'error';
  }
  setTimeout(() => { if (message.type === 'success' || message.type === 'warning' || message.type === 'error') message.text = ''; }, 3000);
};
const copyAliTokenToClipboard = async () => {
  if (!config.aliyundrive_token) { message.text = 'Token为空，无法复制。'; message.type = 'warning'; return; }
  try {
    await navigator.clipboard.writeText(config.aliyundrive_token);
    message.text = '阿里云盘Token已复制到剪贴板！';
    message.type = 'success';
  } catch (err) {
    console.error('复制Token失败:', err);
    message.text = '复制Token失败。请检查浏览器权限或手动复制。';
    message.type = 'error';
  }
  setTimeout(() => { message.text = ''; }, 3000);
};

const openQrCodeDialog = () => {
  qrDialog.show = true;
  qrDialog.loading = false;
  qrDialog.error = null;
  qrDialog.qrcode = '';
  qrDialog.uid = '';
  qrDialog.time = '';
  qrDialog.sign = '';
  if (!clientTypes.some(ct => ct.value === qrDialog.clientType)) qrDialog.clientType = 'alipaymini';
  const selectedClient = clientTypes.find(type => type.value === qrDialog.clientType);
  qrDialog.tips = selectedClient ? `请使用${selectedClient.label}扫描二维码登录` : '请使用支付宝扫描二维码登录';
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
  console.warn(`【115STRM助手 DEBUG】准备获取二维码，前端选择的 clientType: ${qrDialog.clientType}`);
  try {
    const response = await props.api.get(`plugin/${PLUGIN_ID}/get_qrcode?client_type=${qrDialog.clientType}`);
    if (response && response.code === 0 && response.data) {
      qrDialog.uid = response.data.uid;
      qrDialog.time = response.data.time;
      qrDialog.sign = response.data.sign;
      qrDialog.qrcode = response.data.qrcode;
      qrDialog.tips = response.data.tips || '请扫描二维码登录';
      qrDialog.status = '等待扫码';
      if (response.data.client_type) qrDialog.clientType = response.data.client_type;
      startQrCodeCheckInterval();
    } else {
      qrDialog.error = response?.msg || '获取二维码失败';
      console.error("【115STRM助手 DEBUG】获取二维码API调用失败或返回错误码: ", response);
    }
  } catch (err) {
    qrDialog.error = `获取二维码出错: ${err.message || '未知错误'}`;
    console.error('【115STRM助手 DEBUG】获取二维码 JS 捕获异常:', err);
  } finally {
    qrDialog.loading = false;
  }
};

const checkQrCodeStatus = async () => {
  if (!qrDialog.uid || !qrDialog.show || !qrDialog.time || !qrDialog.sign) return;
  try {
    const response = await props.api.get(`plugin/${PLUGIN_ID}/check_qrcode?uid=${qrDialog.uid}&time=${qrDialog.time}&sign=${qrDialog.sign}&client_type=${qrDialog.clientType}`);
    if (response && response.code === 0 && response.data) {
      const data = response.data;
      if (data.status === 'waiting') qrDialog.status = '等待扫码';
      else if (data.status === 'scanned') qrDialog.status = '已扫码，请在设备上确认';
      else if (data.status === 'success') {
        if (data.cookie) {
          clearQrCodeCheckInterval();
          qrDialog.status = '登录成功！';
          config.cookies = data.cookie;
          message.text = '登录成功！Cookie已获取，请点击下方"保存配置"按钮保存。';
          message.type = 'success';
          setTimeout(() => { qrDialog.show = false; }, 3000);
        } else {
          qrDialog.status = '登录似乎成功，但未获取到Cookie';
          message.text = '登录成功但未获取到Cookie信息，请重试或检查账号。';
          message.type = 'warning';
          clearQrCodeCheckInterval();
        }
      }
    } else if (response) {
      if (qrDialog.status !== '登录成功，正在处理...') {
        clearQrCodeCheckInterval();
        qrDialog.error = response.msg || '二维码已失效，请刷新';
        qrDialog.status = '二维码已失效';
      }
    }
  } catch (err) {
    if (qrDialog.status !== '登录成功，正在处理...') console.error('检查二维码状态JS捕获异常:', err);
  }
};

const startQrCodeCheckInterval = () => {
  clearQrCodeCheckInterval();
  qrDialog.checkIntervalId = setInterval(checkQrCodeStatus, 3000);
};

const openAliQrCodeDialog = () => {
  aliQrDialog.show = true;
  aliQrDialog.loading = false;
  aliQrDialog.error = null;
  aliQrDialog.qrcode = '';
  aliQrDialog.t = '';
  aliQrDialog.ck = '';
  aliQrDialog.status = '等待扫码';
  getAliQrCode();
};

const getAliQrCode = async () => {
  aliQrDialog.loading = true;
  aliQrDialog.error = null;
  aliQrDialog.qrcode = '';
  try {
    const response = await props.api.get(`plugin/${PLUGIN_ID}/get_aliyundrive_qrcode`);
    if (response && response.code === 0 && response.data) {
      aliQrDialog.qrcode = response.data.qrcode;
      aliQrDialog.t = response.data.t;
      aliQrDialog.ck = response.data.ck;
      aliQrDialog.status = '等待扫码';
      startAliQrCodeCheckInterval();
    } else {
      aliQrDialog.error = response?.msg || '获取阿里云盘二维码失败';
    }
  } catch (err) {
    aliQrDialog.error = `获取二维码出错: ${err.message || '未知错误'}`;
  } finally {
    aliQrDialog.loading = false;
  }
};

const checkAliQrCodeStatus = async () => {
  if (!aliQrDialog.t || !aliQrDialog.ck || !aliQrDialog.show) return;
  try {
    const response = await props.api.get(`plugin/${PLUGIN_ID}/check_aliyundrive_qrcode?t=${aliQrDialog.t}&ck=${encodeURIComponent(aliQrDialog.ck)}`);
    if (response && response.code === 0 && response.data) {
      if (response.data.status === 'success' && response.data.token) {
        clearAliQrCodeCheckInterval();
        aliQrDialog.status = '登录成功！';
        config.aliyundrive_token = response.data.token;
        message.text = '阿里云盘登录成功！Token已获取，请点击下方“保存配置”按钮。';
        message.type = 'success';
        setTimeout(() => { aliQrDialog.show = false; }, 2000);
      } else {
        aliQrDialog.status = response.data.msg || '等待扫码';
        if (response.data.status === 'expired' || response.data.status === 'invalid') {
          clearAliQrCodeCheckInterval();
          aliQrDialog.error = '二维码已失效，请刷新';
        }
      }
    } else if (response) {
      clearAliQrCodeCheckInterval();
      aliQrDialog.status = '二维码已失效';
      aliQrDialog.error = response.msg || '二维码检查失败，请刷新。';
    }
  } catch (err) {
    console.error('检查阿里云盘二维码状态出错:', err);
  }
};

const startAliQrCodeCheckInterval = () => {
  clearAliQrCodeCheckInterval();
  aliQrDialog.checkIntervalId = setInterval(checkAliQrCodeStatus, 2000);
};
const clearAliQrCodeCheckInterval = () => {
  if (aliQrDialog.checkIntervalId) {
    clearInterval(aliQrDialog.checkIntervalId);
    aliQrDialog.checkIntervalId = null;
  }
};
const refreshAliQrCode = () => {
  clearAliQrCodeCheckInterval();
  aliQrDialog.error = null;
  getAliQrCode();
};
const closeAliQrCodeDialog = () => {
  clearAliQrCodeCheckInterval();
  aliQrDialog.show = false;
};

// emby2Alist 配置生成相关函数
const openConfigGeneratorDialog = async () => {
  configGeneratorDialog.show = true;
  configGeneratorDialog.mountDir = config.fuse_strm_mount_dir || '/emby/115';
  configGeneratorDialog.moviepilotAddress = config.moviepilot_address || window.location.origin || 'http://localhost:3000';
  configGeneratorDialog.generatedConfig = ''; // 清空之前的配置

  // 从后端 API 获取生成的配置
  await generateConfig();
};

const closeConfigGeneratorDialog = () => {
  configGeneratorDialog.show = false;
};

const generateConfig = async () => {
  try {
    configGeneratorDialog.loading = true;
    const mountDir = configGeneratorDialog.mountDir || '/emby/115';
    const moviepilotAddress = configGeneratorDialog.moviepilotAddress || window.location.origin || 'http://localhost:3000';

    // 从后端 API 获取生成的配置
    const response = await props.api.get(
      `plugin/${PLUGIN_ID}/generate_emby2alist_config?mount_dir=${encodeURIComponent(mountDir)}&moviepilot_address=${encodeURIComponent(moviepilotAddress)}`
    );

    if (response && response.code === 0 && response.data) {
      configGeneratorDialog.generatedConfig = response.data.generated_config || '';
      // 更新显示的值（如果后端返回了不同的值）
      if (response.data.mount_dir) {
        configGeneratorDialog.mountDir = response.data.mount_dir;
      }
      if (response.data.moviepilot_address) {
        configGeneratorDialog.moviepilotAddress = response.data.moviepilot_address;
      }
      message.text = '配置生成成功！';
      message.type = 'success';
      setTimeout(() => {
        if (message.type === 'success') {
          message.text = '';
        }
      }, 3000);
    } else {
      message.text = response?.msg || '生成配置失败';
      message.type = 'error';
      setTimeout(() => {
        if (message.type === 'error') {
          message.text = '';
        }
      }, 3000);
    }
  } catch (err) {
    console.error('生成配置失败:', err);
    message.text = `生成配置失败: ${err.message || '未知错误'}`;
    message.type = 'error';
    setTimeout(() => {
      if (message.type === 'error') {
        message.text = '';
      }
    }, 3000);
  } finally {
    configGeneratorDialog.loading = false;
  }
};

const copyGeneratedConfig = async () => {
  try {
    await navigator.clipboard.writeText(configGeneratorDialog.generatedConfig);
    message.text = '配置已复制到剪贴板！';
    message.type = 'success';
    setTimeout(() => {
      if (message.type === 'success') {
        message.text = '';
      }
    }, 3000);
  } catch (err) {
    message.text = '复制失败，请手动复制';
    message.type = 'error';
    setTimeout(() => {
      if (message.type === 'error') {
        message.text = '';
      }
    }, 3000);
  }
};

// 生活事件故障检查
const checkLifeEventStatus = async () => {
  lifeEventCheckDialog.show = true;
  lifeEventCheckDialog.loading = true;
  lifeEventCheckDialog.error = null;
  lifeEventCheckDialog.result = null;

  try {
    const response = await props.api.post(`plugin/${PLUGIN_ID}/check_life_event_status`);
    if (response.code === 0) {
      lifeEventCheckDialog.result = response;
    } else {
      lifeEventCheckDialog.error = response.msg || '检查失败';
    }
  } catch (error) {
    lifeEventCheckDialog.error = error.message || '检查时发生错误';
  } finally {
    lifeEventCheckDialog.loading = false;
  }
};

const closeLifeEventCheckDialog = () => {
  lifeEventCheckDialog.show = false;
  lifeEventCheckDialog.error = null;
  lifeEventCheckDialog.result = null;
};

const copyDebugInfo = async () => {
  if (lifeEventCheckDialog.result?.data?.debug_info) {
    try {
      await navigator.clipboard.writeText(lifeEventCheckDialog.result.data.debug_info);
      message.text = '调试信息已复制到剪贴板';
      message.type = 'success';
    } catch (error) {
      message.text = '复制失败，请手动选择文本复制';
      message.type = 'error';
    }
  }
};

const clearQrCodeCheckInterval = () => {
  if (qrDialog.checkIntervalId) {
    clearInterval(qrDialog.checkIntervalId);
    qrDialog.checkIntervalId = null;
  }
};
const refreshQrCode = () => {
  clearQrCodeCheckInterval();
  qrDialog.error = null;
  const matchedType = clientTypes.find(type => type.value === qrDialog.clientType);
  qrDialog.tips = matchedType ? `请使用${matchedType.label}扫描二维码登录` : '请扫描二维码登录';
  getQrCode();
};
const closeQrDialog = () => {
  clearQrCodeCheckInterval();
  qrDialog.show = false;
};

onMounted(async () => {
  ensureSentryInitialized();
  await loadConfig();
  await checkTransferModuleEnhancement();
});

onBeforeUnmount(() => {
  console.log('组件即将卸载，清理定时器...');
  clearQrCodeCheckInterval();
  clearAliQrCodeCheckInterval();
});

// 监听基础设置折叠状态变化并保存到 localStorage
watch(basicConfigExpanded, (newVal) => {
  localStorage.setItem('p115strmhelper_basic_config_expanded', JSON.stringify(newVal));
}, { deep: true });

watch(() => qrDialog.clientType, (newVal, oldVal) => {
  if (newVal !== oldVal && qrDialog.show) {
    console.log(`【115STRM助手 DEBUG】qrDialog.clientType 从 ${oldVal} 变为 ${newVal}，准备刷新二维码`);
    refreshQrCode();
  }
});

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

const openExcludeDirSelector = (configKeyToUpdate) => {
  dirDialog.show = true;
  dirDialog.isLocal = true;
  dirDialog.loading = false;
  dirDialog.error = null;
  dirDialog.items = [];
  dirDialog.currentPath = '/';
  dirDialog.originalPathTypeBackup = dirDialog.type;
  dirDialog.originalIndexBackup = dirDialog.index;
  dirDialog.targetConfigKeyForExclusion = configKeyToUpdate;
  dirDialog.type = 'excludePath';
  dirDialog.index = -1;
  loadDirContent();
};

const removeExcludePathEntry = (index, type) => {
  let targetArrayRef;
  if (type === 'transfer_exclude') targetArrayRef = transferExcludePaths;
  else if (type === 'life_exclude') targetArrayRef = monitorLifeExcludePaths;
  else if (type === 'increment_exclude') targetArrayRef = incrementSyncExcludePaths;
  if (targetArrayRef && targetArrayRef.value && index < targetArrayRef.value.length) {
    targetArrayRef.value.splice(index, 1);
    if (targetArrayRef.value.length === 0) targetArrayRef.value = [{ path: '' }];
  }
};
</script>

<style scoped>
/* ============================================
   动画关键帧定义
   ============================================ */

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

/* 按钮脉冲动画 */
@keyframes buttonPulse {

  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(91, 207, 250, 0.4);
  }

  50% {
    box-shadow: 0 0 0 8px rgba(91, 207, 250, 0);
  }
}

/* 图标弹跳动画 */
@keyframes iconBounce {

  0%,
  100% {
    transform: scale(1);
  }

  50% {
    transform: scale(1.2);
  }
}

/* 标签页滑入动画 */
@keyframes tabSlideIn {
  0% {
    opacity: 0;
    transform: translateY(10px);
  }

  100% {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 输入框聚焦发光动画 */
@keyframes inputGlow {

  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(91, 207, 250, 0.3);
  }

  50% {
    box-shadow: 0 0 0 4px rgba(91, 207, 250, 0.1);
  }
}

/* 优化基础设置折叠面板动画速度 */
:v-deep(.v-expansion-panel) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

:v-deep(.v-expansion-panel-text__wrapper) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

:v-deep(.v-expansion-panel__shadow) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

/* 统一字体 - 现代字体栈 */
:v-deep(.v-card-title),
:v-deep(.v-card-text),
:v-deep(.v-list-item-title),
:v-deep(.v-list-item-subtitle),
:v-deep(.v-alert),
:v-deep(.v-btn),
:v-deep(.text-caption),
:v-deep(.text-subtitle-1),
:v-deep(.text-body-1),
:v-deep(.text-body-2) {
  font-family: 'Inter', 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif !important;
}

/* 标题字体优化 */
:v-deep(.v-card-title) {
  font-weight: 700 !important;
  font-size: 1.1rem !important;
  letter-spacing: -0.02em !important;
  line-height: 1.3 !important;
}

:v-deep(.text-subtitle-1) {
  font-weight: 600 !important;
  font-size: 1rem !important;
  letter-spacing: -0.01em !important;
  line-height: 1.4 !important;
}

:v-deep(.text-subtitle-2) {
  font-weight: 600 !important;
  font-size: 0.9rem !important;
  letter-spacing: 0 !important;
  line-height: 1.35 !important;
}

/* 正文字体优化 */
:v-deep(.text-body-1) {
  font-weight: 400 !important;
  font-size: 1rem !important;
  line-height: 1.6 !important;
  letter-spacing: 0 !important;
}

:v-deep(.text-body-2) {
  font-weight: 400 !important;
  font-size: 0.9rem !important;
  line-height: 1.5 !important;
  letter-spacing: 0 !important;
}

/* 小字字体优化 */
:v-deep(.text-caption) {
  font-weight: 400 !important;
  font-size: 0.8rem !important;
  line-height: 1.4 !important;
  letter-spacing: 0.01em !important;
}

/* 列表项字体优化 */
:v-deep(.v-list-item-title) {
  font-weight: 500 !important;
  font-size: 0.9rem !important;
  line-height: 1.4 !important;
}

:v-deep(.v-list-item-subtitle) {
  font-weight: 400 !important;
  font-size: 0.8rem !important;
  line-height: 1.4 !important;
  letter-spacing: 0.01em !important;
}

/* 按钮字体优化 */
:v-deep(.v-btn) {
  font-weight: 500 !important;
  font-size: 0.875rem !important;
  letter-spacing: 0.02em !important;
  text-transform: none !important;
}

/* 标签字体优化 */
:v-deep(.v-tab) {
  font-weight: 500 !important;
  font-size: 0.875rem !important;
  letter-spacing: 0.01em !important;
}

/* 输入框字体优化 */
:v-deep(.v-field__input) {
  font-weight: 400 !important;
  font-size: 0.9rem !important;
  letter-spacing: 0 !important;
}

:v-deep(.v-label) {
  font-weight: 500 !important;
  font-size: 0.85rem !important;
  letter-spacing: 0.01em !important;
}

/* 警告/提示字体优化 */
:v-deep(.v-alert) {
  font-weight: 400 !important;
  font-size: 0.85rem !important;
  line-height: 1.5 !important;
}

/* 卡片入场动画 */
.plugin-config :deep(.v-card) {
  animation: cardEnter 0.6s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
}

/* 配置卡片悬停动画优化 */
.config-card {
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform, box-shadow;
}

.config-card:hover {
  transform: translateY(-6px) scale(1.015);
  box-shadow:
    0 16px 40px rgba(91, 207, 250, 0.35),
    0 6px 16px rgba(245, 171, 185, 0.25),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.1) !important;
}

/* 按钮动画优化 */
:v-deep(.v-btn) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform, box-shadow;
}

:v-deep(.v-btn:hover) {
  transform: translateY(-3px) scale(1.05);
  box-shadow: 0 8px 20px rgba(91, 207, 250, 0.35) !important;
}

:v-deep(.v-btn:active) {
  transform: scale(0.98) translateY(-1px);
  transition: all 0.1s ease !important;
}

/* 主要按钮脉冲效果 */
:v-deep(.v-btn.color-primary:not(:hover)) {
  animation: buttonPulse 2s ease-in-out infinite;
}

/* 图标动画优化 */
:v-deep(.v-icon) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform;
}

:v-deep(.v-btn:hover .v-icon) {
  transform: scale(1.15) rotate(5deg);
}

:v-deep(.v-tab:hover .v-icon) {
  animation: iconBounce 0.5s ease;
}

/* 标签页动画优化 */
.main-category-tabs {
  animation: tabSlideIn 0.5s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
}

:v-deep(.main-category-tabs .v-tab) {
  transition: all 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform, background;
}

:v-deep(.main-category-tabs .v-tab:hover) {
  transform: translateY(-3px) scale(1.02);
}

:v-deep(.main-category-tabs .v-tab--selected) {
  animation: buttonPulse 2s ease-in-out infinite;
}

/* 子标签页动画 */
:v-deep(.sub-category-tabs .v-tab) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

:v-deep(.sub-category-tabs .v-tab:hover) {
  transform: translateY(-2px);
}

/* 输入框动画优化 */
:v-deep(.v-field) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: box-shadow, border-color;
}

:v-deep(.v-field--focused) {
  animation: inputGlow 1.5s ease-in-out infinite;
}

/* 开关动画优化 */
:v-deep(.v-switch__track) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

:v-deep(.v-switch__thumb) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

/* 列表项动画优化 */
:v-deep(.v-list-item) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform, background;
}

:v-deep(.v-list-item:hover) {
  transform: translateX(5px);
}

/* 芯片动画优化 */
:v-deep(.v-chip) {
  transition: all 0.25s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform, box-shadow;
}

:v-deep(.v-chip:hover) {
  transform: scale(1.08) translateY(-2px);
  box-shadow: 0 4px 12px rgba(91, 207, 250, 0.25) !important;
}

/* 对话框动画优化 */
:v-deep(.v-dialog .v-overlay__content) {
  animation: cardEnter 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
}

/* 警告框动画优化 */
:v-deep(.v-alert) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

:v-deep(.v-alert:hover) {
  transform: translateY(-2px);
  box-shadow: 0 8px 20px rgba(91, 207, 250, 0.2) !important;
}

/* 展开面板动画优化 */
:v-deep(.v-expansion-panel-title) {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

:v-deep(.v-expansion-panel-title:hover) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.1) 0%,
      rgba(245, 171, 185, 0.08) 100%) !important;
}

/* 路径组动画优化 */
.path-group {
  transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
  will-change: transform, box-shadow;
}

.path-group:hover {
  transform: translateY(-3px);
  box-shadow: 0 8px 20px rgba(91, 207, 250, 0.15) !important;
}

/* 进度条动画优化 */
:v-deep(.v-progress-linear) {
  transition: all 0.3s ease !important;
}

:v-deep(.v-progress-linear__determinate) {
  transition: transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1) !important;
}

/* 选择器动画优化 */
:v-deep(.v-select__selection) {
  transition: all 0.2s ease !important;
}

/* 菜单动画优化 */
:v-deep(.v-menu .v-overlay__content) {
  animation: cardEnter 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) forwards;
}

/* 滚动条动画优化 */
:v-deep(::-webkit-scrollbar-thumb) {
  transition: all 0.3s ease !important;
}

:v-deep(::-webkit-scrollbar-thumb:hover) {
  background: linear-gradient(135deg, rgba(91, 207, 250, 0.9), rgba(245, 171, 185, 0.9)) !important;
}

:deep(.v-expansion-panel-text__wrapper) {
  transition: all 0.2s ease !important;
}

:deep(.v-expansion-panel__shadow) {
  transition: all 0.2s ease !important;
}

/* 统一字体 - Inspired by Page.vue */
:deep(.v-card-title),
:deep(.v-card-text),
:deep(.v-list-item-title),
:deep(.v-list-item-subtitle),
:deep(.v-alert),
:deep(.v-btn),
:deep(.text-caption),
:deep(.text-subtitle-1),
:deep(.text-body-1),
:deep(.text-body-2) {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif !important;
}

/* 文字大小 - Unified with Page.vue */
:deep(.text-caption) {
  font-size: 0.8rem !important;
}

:deep(.text-body-2) {
  font-size: 0.85rem !important;
}

:deep(.text-subtitle-2) {
  /* Added for consistency with Page.vue inner card titles */
  font-size: 0.875rem !important;
  font-weight: 500 !important;
  line-height: 1.25rem !important;
}

:deep(.v-list-item-title) {
  font-size: 0.85rem !important;
  /* Unified with Page.vue's common list item title size */
}

:deep(.v-list-item-subtitle) {
  font-size: 0.8rem !important;
  /* Unified with Page.vue's common list item subtitle size */
}

/* ============================================
   配置界面样式 - 镜面效果 + 蓝粉白配色
   ============================================ */

/* 主容器 */
.plugin-config {
  padding: 12px;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}

.plugin-config :deep(.v-card) {
  border-radius: 20px !important;
  overflow: hidden;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  /* 镜面效果 - 动态适配主题 */
  background: rgba(var(--v-theme-surface), 0.7) !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  box-shadow:
    0 8px 32px rgba(91, 207, 250, 0.25),
    0 2px 8px rgba(245, 171, 185, 0.2),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.05) !important;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12) !important;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

/* 主卡片高度设置 */
.config-main-card {
  max-height: 85vh;
}

@media (max-width: 768px) {
  .config-main-card {
    max-height: 100vh;
    height: 100vh;
  }
}

/* 暗色模式下的主卡片 */
:deep(.v-theme--dark) .plugin-config .v-card,
:deep([data-theme="dark"]) .plugin-config .v-card {
  background: rgba(var(--v-theme-surface), 0.75) !important;
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  box-shadow:
    0 8px 32px rgba(91, 207, 250, 0.3),
    0 2px 8px rgba(245, 171, 185, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
}

.config-card {
  border-radius: 16px !important;
  /* 镜面效果 - 动态适配主题 */
  background: rgba(var(--v-theme-surface), 0.65) !important;
  backdrop-filter: blur(15px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(15px) saturate(180%) !important;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12) !important;
  overflow: hidden;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1) !important;
  margin-bottom: 16px !important;
  box-shadow:
    0 4px 16px rgba(91, 207, 250, 0.2),
    0 1px 4px rgba(245, 171, 185, 0.15),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.05) !important;
}

/* 暗色模式下的配置卡片 */
:deep(.v-theme--dark) .config-card,
:deep([data-theme="dark"]) .config-card {
  background: rgba(var(--v-theme-surface), 0.7) !important;
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  box-shadow:
    0 4px 16px rgba(91, 207, 250, 0.25),
    0 1px 4px rgba(245, 171, 185, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
}

.config-card:hover {
  transform: translateY(-4px) scale(1.01);
  box-shadow:
    0 12px 32px rgba(91, 207, 250, 0.3),
    0 4px 12px rgba(245, 171, 185, 0.25),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.08) !important;
  border-color: rgba(91, 207, 250, 0.5) !important;
  background: rgba(var(--v-theme-surface), 0.75) !important;
}

/* 暗色模式下的配置卡片悬停状态 */
:deep(.v-theme--dark) .config-card:hover,
:deep([data-theme="dark"]) .config-card:hover {
  background: rgba(var(--v-theme-surface), 0.8) !important;
  border-color: rgba(91, 207, 250, 0.6) !important;
  box-shadow:
    0 12px 32px rgba(91, 207, 250, 0.4),
    0 4px 12px rgba(245, 171, 185, 0.3),
    inset 0 1px 0 rgba(255, 255, 255, 0.15) !important;
}

/* ============================================
   主分类标签样式优化
   ============================================ */
.main-category-tabs {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.08) 0%,
      rgba(245, 171, 185, 0.06) 50%,
      rgba(255, 184, 201, 0.04) 100%) !important;
  border-radius: 16px 16px 0 0 !important;
  padding: 4px 8px !important;
  display: flex;
  align-items: center;
}

:deep(.main-category-tabs .v-tab) {
  border-radius: 12px !important;
  margin: 4px 2px !important;
  padding: 10px 20px !important;
  font-size: 0.875rem !important;
  font-weight: 500 !important;
  text-transform: none !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  min-height: 44px !important;
  color: rgba(var(--v-theme-on-surface), 0.7) !important;
  display: flex;
  align-items: center;
  justify-content: center;
}

:deep(.main-category-tabs .v-tab:hover) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.15) 0%,
      rgba(245, 171, 185, 0.12) 100%) !important;
  color: rgba(var(--v-theme-primary), 0.95) !important;
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(91, 207, 250, 0.2),
    0 1px 3px rgba(245, 171, 185, 0.15) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

:deep(.main-category-tabs .v-tab--selected) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.25) 0%,
      rgba(245, 171, 185, 0.2) 100%) !important;
  color: rgb(var(--v-theme-primary)) !important;
  font-weight: 600 !important;
  box-shadow: 0 3px 12px rgba(91, 207, 250, 0.35),
    0 2px 6px rgba(245, 171, 185, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.15) !important;
  position: relative;
  backdrop-filter: blur(12px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(12px) saturate(180%) !important;
}

/* 隐藏Vuetify默认滑块 */
:deep(.main-category-tabs .v-tab__slider) {
  display: none !important;
}


:deep(.main-category-tabs .v-tab .v-icon) {
  margin-right: 8px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

:deep(.main-category-tabs .v-tab:hover .v-icon) {
  color: rgba(var(--v-theme-primary), 0.8);
  transform: scale(1.05);
}

:deep(.main-category-tabs .v-tab--selected .v-icon) {
  transform: scale(1.15);
  color: rgb(var(--v-theme-primary));
}

:deep(.main-category-tabs .v-slider) {
  display: none !important;
}

/* ============================================
   子标签页样式优化
   ============================================ */
.sub-category-tabs {
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  padding: 8px 12px !important;
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.05) 0%,
      rgba(245, 171, 185, 0.03) 50%,
      rgba(255, 184, 201, 0.02) 100%) !important;
  border-radius: 0 !important;
  display: flex;
  align-items: center;
  backdrop-filter: blur(8px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(8px) saturate(180%) !important;
}

:deep(.sub-category-tabs .v-tab) {
  min-width: auto !important;
  padding: 10px 18px !important;
  white-space: nowrap;
  flex-shrink: 0;
  font-size: 0.875rem !important;
  line-height: 1.5 !important;
  border-radius: 10px 10px 0 0 !important;
  margin: 0 4px !important;
  text-transform: none !important;
  font-weight: 500 !important;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
  color: rgba(var(--v-theme-on-surface), 0.65) !important;
  position: relative;
  overflow: visible !important;
  display: flex;
  align-items: center;
  justify-content: center;
}


:deep(.sub-category-tabs .v-tab:hover) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.12) 0%,
      rgba(245, 171, 185, 0.1) 100%) !important;
  color: rgba(var(--v-theme-primary), 0.9) !important;
  transform: translateY(-1px);
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

:deep(.sub-category-tabs .v-tab--selected) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.15) 0%,
      rgba(245, 171, 185, 0.12) 100%) !important;
  color: rgb(var(--v-theme-primary)) !important;
  font-weight: 600 !important;
  box-shadow: 0 -2px 12px rgba(91, 207, 250, 0.2),
    0 -1px 4px rgba(245, 171, 185, 0.15),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
  position: relative;
  backdrop-filter: blur(10px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(10px) saturate(180%) !important;
}

/* 隐藏Vuetify默认滑块 */
:deep(.sub-category-tabs .v-tab__slider) {
  display: none !important;
}


:deep(.sub-category-tabs .v-tab .v-icon) {
  margin-right: 6px;
  flex-shrink: 0;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  color: rgba(var(--v-theme-on-surface), 0.55);
}

:deep(.sub-category-tabs .v-tab:hover .v-icon) {
  color: rgba(var(--v-theme-primary), 0.75);
  transform: scale(1.05);
}

:deep(.sub-category-tabs .v-tab--selected .v-icon) {
  transform: scale(1.12);
  color: rgb(var(--v-theme-primary));
}

:deep(.sub-category-tabs .v-tab__content) {
  display: flex;
  align-items: center;
  white-space: nowrap;
}

:deep(.sub-category-tabs .v-slider) {
  display: none !important;
}

/* 确保子标签页容器有足够空间 */
:deep(.v-window-item[value^="category-"] .v-card-text) {
  min-height: 0;
}

/* 优化分隔线样式 - 符合主题色 */
:deep(.config-card .v-divider) {
  border-color: rgba(91, 207, 250, 0.2) !important;
  background: linear-gradient(90deg,
      transparent 0%,
      rgba(91, 207, 250, 0.3) 20%,
      rgba(245, 171, 185, 0.3) 80%,
      transparent 100%) !important;
  height: 2px !important;
  opacity: 1 !important;
}

:deep(.v-theme--dark) .config-card .v-divider,
:deep([data-theme="dark"]) .config-card .v-divider {
  border-color: rgba(91, 207, 250, 0.3) !important;
  background: linear-gradient(90deg,
      transparent 0%,
      rgba(91, 207, 250, 0.4) 20%,
      rgba(245, 171, 185, 0.4) 80%,
      transparent 100%) !important;
}

/* 暗色模式下的标签页优化 */
:deep(.v-theme--dark) .main-category-tabs,
:deep([data-theme="dark"]) .main-category-tabs {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.12) 0%,
      rgba(245, 171, 185, 0.1) 50%,
      rgba(255, 184, 201, 0.08) 100%) !important;
}

:deep(.v-theme--dark) .sub-category-tabs,
:deep([data-theme="dark"]) .sub-category-tabs {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.08) 0%,
      rgba(245, 171, 185, 0.06) 50%,
      rgba(255, 184, 201, 0.04) 100%) !important;
}

:deep(.v-theme--dark) .sub-category-tabs .v-tab--selected,
:deep([data-theme="dark"]) .sub-category-tabs .v-tab--selected {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.2) 0%,
      rgba(245, 171, 185, 0.15) 100%) !important;
  box-shadow: 0 -2px 12px rgba(91, 207, 250, 0.25),
    0 -1px 4px rgba(245, 171, 185, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.15) !important;
}



.bg-primary-gradient,
.bg-primary-lighten-5 {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.25) 0%,
      rgba(245, 171, 185, 0.2) 50%,
      rgba(255, 184, 201, 0.15) 100%) !important;
  backdrop-filter: blur(20px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(20px) saturate(180%) !important;
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.12) !important;
  box-shadow: inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.05) !important;
}

/* 暗色模式下的标题区域 */
:deep(.v-theme--dark) .bg-primary-gradient,
:deep(.v-theme--dark) .bg-primary-lighten-5,
:deep([data-theme="dark"]) .bg-primary-gradient,
:deep([data-theme="dark"]) .bg-primary-lighten-5 {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.3) 0%,
      rgba(245, 171, 185, 0.25) 50%,
      rgba(255, 184, 201, 0.2) 100%) !important;
  border-bottom: 1px solid rgba(255, 255, 255, 0.15) !important;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
}

.plugin-config :deep(.v-card-title) {
  border-radius: 12px 12px 0 0;
}

.config-title {
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), var(--v-high-emphasis-opacity));
}

/* 路径输入框组 - 镜面效果，动态适配主题 */
.path-group {
  padding: 12px;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12) !important;
  border-radius: 12px;
  background: rgba(var(--v-theme-surface), 0.5) !important;
  backdrop-filter: blur(10px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(10px) saturate(180%) !important;
  box-shadow:
    0 2px 8px rgba(91, 207, 250, 0.15),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.05) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.path-group:hover {
  border-color: rgba(91, 207, 250, 0.4) !important;
  background: rgba(var(--v-theme-surface), 0.65) !important;
  box-shadow:
    0 4px 12px rgba(91, 207, 250, 0.2),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.08) !important;
}

/* 暗色模式下的路径输入框组 */
:deep(.v-theme--dark) .path-group,
:deep([data-theme="dark"]) .path-group {
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  background: rgba(var(--v-theme-surface), 0.6) !important;
}

:deep(.v-theme--dark) .path-group:hover,
:deep([data-theme="dark"]) .path-group:hover {
  background: rgba(var(--v-theme-surface), 0.7) !important;
  box-shadow:
    0 4px 12px rgba(91, 207, 250, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
}

.path-input-row {
  display: flex;
  align-items: center;
}

.path-input-field {
  flex-grow: 1;
}

.path-input-action {
  margin-left: 8px;
}

.v-list-item-title.text-danger {
  color: rgb(var(--v-theme-error)) !important;
  font-weight: bold;
}

/* Cookie 输入框样式 */
:deep(.v-textarea .v-field__input) {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace;
  font-size: 0.8rem;
  line-height: 1.4;
}

/* Tab 样式调整 - 蓝粉配色 */
:deep(.v-tabs) {
  border-bottom: 2px solid rgba(91, 207, 250, 0.4) !important;
  border-radius: 12px 12px 0 0;
  background: rgba(var(--v-theme-surface), 0.3) !important;
  backdrop-filter: blur(10px) !important;
}

/* 暗色模式下的 Tab 背景 */
:deep(.v-theme--dark) .v-tabs,
:deep([data-theme="dark"]) .v-tabs {
  background: rgba(var(--v-theme-surface), 0.4) !important;
  border-bottom-color: rgba(91, 207, 250, 0.5) !important;
}

/* Tab 容器 - 确保没有白色背景，动态适配主题 */
:deep(.v-tabs .v-tabs-bar),
:deep(.v-tabs .v-tabs-bar__content),
:deep(.v-tabs .v-tabs-bar__wrapper) {
  background: transparent !important;
}

/* Tab 项背景 - 确保未选中状态在暗色模式下有合适的背景 */
:deep(.v-tab) {
  background: transparent !important;
}

/* 暗色模式下的未选中 Tab */
:deep(.v-theme--dark) .v-tab:not(.v-tab--selected),
:deep([data-theme="dark"]) .v-tab:not(.v-tab--selected) {
  background: rgba(var(--v-theme-surface), 0.15) !important;
  backdrop-filter: blur(5px) !important;
}

/* Tab 切换动画时的中间状态 */
:deep(.v-tab--transitioning),
:deep(.v-tab[aria-selected="true"]) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.3) 0%,
      rgba(245, 171, 185, 0.25) 100%) !important;
}

/* 暗色模式下的 Tab 切换动画 */
:deep(.v-theme--dark) .v-tab--transitioning,
:deep(.v-theme--dark) .v-tab[aria-selected="true"],
:deep([data-theme="dark"]) .v-tab--transitioning,
:deep([data-theme="dark"]) .v-tab[aria-selected="true"] {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.4) 0%,
      rgba(245, 171, 185, 0.35) 100%) !important;
}

:deep(.v-tab) {
  font-weight: 500;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  border-radius: 12px 12px 0 0 !important;
  margin: 0 2px;
  color: rgba(var(--v-theme-on-surface), 0.7) !important;
}

/* 暗色模式下的 Tab 文字颜色 */
:deep(.v-theme--dark) .v-tab,
:deep([data-theme="dark"]) .v-tab {
  color: rgba(255, 255, 255, 0.7) !important;
}

:deep(.v-tab:hover) {
  background: linear-gradient(135deg, rgba(91, 207, 250, 0.2), rgba(245, 171, 185, 0.15)) !important;
  color: #5bcffa !important;
}

/* 暗色模式下的 Tab 悬停状态 */
:deep(.v-theme--dark) .v-tab:hover,
:deep([data-theme="dark"]) .v-tab:hover {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.25) 0%,
      rgba(245, 171, 185, 0.2) 100%) !important;
  color: #5bcffa !important;
}

/* Tab 未选中状态 - 确保在暗色模式下有合适的背景 */
:deep(.v-theme--dark) .v-tab:not(.v-tab--selected),
:deep([data-theme="dark"]) .v-tab:not(.v-tab--selected) {
  background: rgba(var(--v-theme-surface), 0.2) !important;
  backdrop-filter: blur(5px) !important;
}

:deep(.v-theme--dark) .v-tab:not(.v-tab--selected):hover,
:deep([data-theme="dark"]) .v-tab:not(.v-tab--selected):hover {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.2) 0%,
      rgba(245, 171, 185, 0.15) 100%) !important;
}

:deep(.v-tab--selected) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.3) 0%,
      rgba(245, 171, 185, 0.25) 100%) !important;
  color: #5bcffa !important;
  border-radius: 12px 12px 0 0 !important;
  font-weight: 600 !important;
  box-shadow:
    0 2px 8px rgba(91, 207, 250, 0.3),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.08) !important;
  backdrop-filter: blur(10px) !important;
}

/* 暗色模式下的选中 Tab - 使用深色背景而不是白色 */
:deep(.v-theme--dark) .v-tab--selected,
:deep([data-theme="dark"]) .v-tab--selected {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.25) 0%,
      rgba(245, 171, 185, 0.2) 100%) !important;
  box-shadow:
    0 2px 8px rgba(91, 207, 250, 0.3),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
  color: #5bcffa !important;
  backdrop-filter: blur(10px) !important;
}

/* Tab 滑动指示器 - 动态适配主题（覆盖所有可能的类名） */
:deep(.v-tabs-slider),
:deep(.v-tabs-slider-wrapper),
:deep(.v-tabs .v-slider),
:deep(.v-tabs .v-slider__track),
:deep(.v-tabs .v-slider__thumb),
:deep(.v-tabs .v-tabs-bar__slider) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.8) 0%,
      rgba(245, 171, 185, 0.7) 100%) !important;
  border: none !important;
  box-shadow: none !important;
}

/* 暗色模式下的滑动指示器 */
:deep(.v-theme--dark) .v-tabs-slider,
:deep(.v-theme--dark) .v-tabs-slider-wrapper,
:deep(.v-theme--dark) .v-tabs .v-slider,
:deep(.v-theme--dark) .v-tabs .v-slider__track,
:deep(.v-theme--dark) .v-tabs .v-slider__thumb,
:deep(.v-theme--dark) .v-tabs .v-tabs-bar__slider,
:deep([data-theme="dark"]) .v-tabs-slider,
:deep([data-theme="dark"]) .v-tabs-slider-wrapper,
:deep([data-theme="dark"]) .v-tabs .v-slider,
:deep([data-theme="dark"]) .v-tabs .v-slider__track,
:deep([data-theme="dark"]) .v-tabs .v-slider__thumb,
:deep([data-theme="dark"]) .v-tabs .v-tabs-bar__slider {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.9) 0%,
      rgba(245, 171, 185, 0.8) 100%) !important;
  box-shadow:
    0 0 8px rgba(91, 207, 250, 0.5),
    0 0 4px rgba(245, 171, 185, 0.4) !important;
}

/* Tab 背景色 - 动态适配主题 */
:deep(.v-tabs) {
  background: rgba(var(--v-theme-surface), 0.3) !important;
}

/* 暗色模式下的 Tab 背景 */
:deep(.v-theme--dark) .v-tabs,
:deep([data-theme="dark"]) .v-tabs {
  background: rgba(var(--v-theme-surface), 0.4) !important;
}

/* Switch 样式调整 - 动态适配主题 */
:deep(.v-switch .v-selection-control__input > .v-icon) {
  color: rgba(var(--v-theme-medium-emphasis));
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

:deep(.v-switch .v-track) {
  background-color: rgba(var(--v-theme-medium-emphasis), 0.3) !important;
  border-radius: 12px !important;
  opacity: 1 !important;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

/* 暗色模式下的开关轨道 */
:deep(.v-theme--dark) .v-switch .v-track,
:deep([data-theme="dark"]) .v-switch .v-track {
  background-color: rgba(255, 255, 255, 0.2) !important;
}

/* 开关启用状态 - 动态适配主题 */
:deep(.v-switch .v-selection-control--dirty .v-track) {
  background-color: rgb(var(--v-theme-primary)) !important;
}

/* 暗色模式下的开关启用状态 */
:deep(.v-theme--dark) .v-switch .v-selection-control--dirty .v-track,
:deep([data-theme="dark"]) .v-switch .v-selection-control--dirty .v-track {
  background-color: #5bcffa !important;
  box-shadow: 0 0 8px rgba(91, 207, 250, 0.4) !important;
}

/* 调整字体大小 */
:deep(.v-card-text .v-label) {
  font-size: 0.9rem;
  /* 调整标签字体大小 */
}

:deep(.v-card-text .v-input__details) {
  font-size: 0.8rem !important;
  /* Ensure input hints also match .text-caption */
}

:deep(.v-text-field input),
:deep(.v-textarea textarea) {
  font-size: 0.875rem !important;
}

/* 优化输入框样式 - 镜面效果，动态适配主题 */
:deep(.v-field) {
  border-radius: 12px !important;
  background: rgba(var(--v-theme-surface), 0.6) !important;
  backdrop-filter: blur(8px) !important;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  box-shadow:
    0 2px 4px rgba(91, 207, 250, 0.15),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.05) !important;
}

/* 暗色模式下的输入框 */
:deep(.v-theme--dark) .v-field,
:deep([data-theme="dark"]) .v-field {
  background: rgba(var(--v-theme-surface), 0.7) !important;
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  box-shadow:
    0 2px 4px rgba(91, 207, 250, 0.2),
    inset 0 1px 0 rgba(255, 255, 255, 0.05) !important;
}

:deep(.v-field--focused) {
  box-shadow:
    0 0 0 3px rgba(91, 207, 250, 0.3),
    0 4px 12px rgba(245, 171, 185, 0.25),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.08) !important;
  border-color: rgba(91, 207, 250, 0.6) !important;
  background: rgba(var(--v-theme-surface), 0.75) !important;
}

/* 暗色模式下的输入框聚焦状态 */
:deep(.v-theme--dark) .v-field--focused,
:deep([data-theme="dark"]) .v-field--focused {
  background: rgba(var(--v-theme-surface), 0.8) !important;
  box-shadow:
    0 0 0 3px rgba(91, 207, 250, 0.4),
    0 4px 12px rgba(245, 171, 185, 0.35),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
  border-color: rgba(91, 207, 250, 0.7) !important;
}

:deep(.v-select .v-field),
:deep(.v-text-field .v-field),
:deep(.v-textarea .v-field) {
  border-radius: 12px !important;
}

/* 输入框内部元素 - 确保没有白色背景 */
:deep(.v-field__input),
:deep(.v-field__control),
:deep(.v-input__control) {
  background: transparent !important;
}

/* 暗色模式下的输入框内部 */
:deep(.v-theme--dark) .v-field__input,
:deep(.v-theme--dark) .v-field__control,
:deep(.v-theme--dark) .v-input__control,
:deep([data-theme="dark"]) .v-field__input,
:deep([data-theme="dark"]) .v-field__control,
:deep([data-theme="dark"]) .v-input__control {
  background: transparent !important;
  color: rgba(255, 255, 255, 0.87) !important;
}

/* 优化按钮样式 - 镜面效果 */
:deep(.v-btn) {
  border-radius: 12px !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  font-weight: 500 !important;
  text-transform: none !important;
  backdrop-filter: blur(10px) !important;
  box-shadow:
    0 2px 8px rgba(91, 207, 250, 0.2),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.08) !important;
}

:deep(.v-btn:hover) {
  transform: translateY(-2px) scale(1.02);
  box-shadow:
    0 6px 16px rgba(91, 207, 250, 0.35),
    0 2px 8px rgba(245, 171, 185, 0.3),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.1) !important;
}

:deep(.v-btn--variant-elevated),
:deep(.v-btn--variant-flat) {
  background: rgba(var(--v-theme-surface), 0.7) !important;
  backdrop-filter: blur(10px) !important;
}

/* 暗色模式下的按钮 */
:deep(.v-theme--dark) .v-btn--variant-elevated,
:deep(.v-theme--dark) .v-btn--variant-flat,
:deep([data-theme="dark"]) .v-btn--variant-elevated,
:deep([data-theme="dark"]) .v-btn--variant-flat {
  background: rgba(var(--v-theme-surface), 0.8) !important;
}

/* 优化警告框样式 - 镜面效果，动态适配主题 */
:deep(.v-alert) {
  border-radius: 16px !important;
  border-left-width: 4px !important;
  background: rgba(var(--v-theme-surface), 0.7) !important;
  backdrop-filter: blur(15px) saturate(180%) !important;
  -webkit-backdrop-filter: blur(15px) saturate(180%) !important;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12) !important;
  box-shadow:
    0 4px 12px rgba(91, 207, 250, 0.2),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.05) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

/* 暗色模式下的警告框 */
:deep(.v-theme--dark) .v-alert,
:deep([data-theme="dark"]) .v-alert {
  background: rgba(var(--v-theme-surface), 0.8) !important;
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  box-shadow:
    0 4px 12px rgba(91, 207, 250, 0.25),
    inset 0 1px 0 rgba(255, 255, 255, 0.1) !important;
}

/* 优化列表项样式 - 镜面效果 */
:deep(.v-list-item) {
  border-radius: 10px;
  margin: 2px 4px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  background: rgba(255, 255, 255, 0.3) !important;
  backdrop-filter: blur(5px) !important;
}

:deep(.v-list-item:hover) {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.2) 0%,
      rgba(245, 171, 185, 0.15) 100%) !important;
  backdrop-filter: blur(10px) !important;
  transform: translateX(4px);
  box-shadow:
    0 2px 8px rgba(91, 207, 250, 0.25),
    inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.08) !important;
}

/* Reduce vertical padding for columns within rows */
:deep(.v-row > .v-col) {
  padding-top: 4px !important;
  padding-bottom: 4px !important;
}

/* Tab 颜色已在上方更新，移除重复样式 */

/* Colorful Switches */
:deep(.v-switch .v-selection-control--dirty .v-track) {
  opacity: 0.8 !important;
  /* Ensure high opacity for color visibility */
}

:deep(.v-switch .v-selection-control--dirty .v-selection-control__input > .v-icon) {
  color: white !important;
}

/* Primary Color Switch - 动态适配主题 */
:deep(.v-switch[color="primary"] .v-selection-control--dirty .v-track),
:deep(.v-switch[color="primary"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-primary)) !important;
  border-color: rgb(var(--v-theme-primary)) !important;
}

/* 暗色模式下的 Primary Switch */
:deep(.v-theme--dark .v-switch[color="primary"] .v-selection-control--dirty .v-track),
:deep(.v-theme--dark .v-switch[color="primary"] .v-selection-control--dirty .v-switch__track),
:deep([data-theme="dark"] .v-switch[color="primary"] .v-selection-control--dirty .v-track),
:deep([data-theme="dark"] .v-switch[color="primary"] .v-selection-control--dirty .v-switch__track) {
  background-color: #5bcffa !important;
  border-color: #5bcffa !important;
  box-shadow: 0 0 8px rgba(91, 207, 250, 0.4) !important;
}

/* Success Color Switch - 动态适配主题 */
:deep(.v-switch[color="success"] .v-selection-control--dirty .v-track),
:deep(.v-switch[color="success"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-success)) !important;
  border-color: rgb(var(--v-theme-success)) !important;
}

/* 暗色模式下的 Success Switch */
:deep(.v-theme--dark .v-switch[color="success"] .v-selection-control--dirty .v-track),
:deep(.v-theme--dark .v-switch[color="success"] .v-selection-control--dirty .v-switch__track),
:deep([data-theme="dark"] .v-switch[color="success"] .v-selection-control--dirty .v-track),
:deep([data-theme="dark"] .v-switch[color="success"] .v-selection-control--dirty .v-switch__track) {
  box-shadow: 0 0 8px rgba(76, 175, 80, 0.4) !important;
}

/* Info Color Switch */
:deep(.v-switch[color="info"] .v-selection-control--dirty .v-track),
:deep(.v-switch[color="info"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-info)) !important;
  border-color: rgb(var(--v-theme-info)) !important;
}

/* Warning Color Switch */
:deep(.v-switch[color="warning"] .v-selection-control--dirty .v-track),
:deep(.v-switch[color="warning"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-warning)) !important;
  border-color: rgb(var(--v-theme-warning)) !important;
}

/* Error Color Switch */
:deep(.v-switch[color="error"] .v-selection-control--dirty .v-track),
:deep(.v-switch[color="error"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-error)) !important;
  border-color: rgb(var(--v-theme-error)) !important;
}

/* 缓存卡片响应式样式 */
.cache-card {
  min-height: 200px;
}

/* 移动端优化 - 保持镜面效果 */
@media (max-width: 959px) {
  .plugin-config {
    padding: 8px;
    width: 100%;
    max-width: 100vw;
    box-sizing: border-box;
  }

  /* 移动端减小圆角但保持镜面效果 */
  .plugin-config :deep(.v-card) {
    border-radius: 16px !important;
    backdrop-filter: blur(15px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(15px) saturate(180%) !important;
  }

  .config-card {
    border-radius: 14px !important;
    margin-bottom: 12px !important;
    backdrop-filter: blur(12px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(12px) saturate(180%) !important;
  }

  .plugin-config :deep(.v-card-title) {
    border-radius: 10px 10px 0 0;
    padding: 12px !important;
  }

  /* 优化触摸目标大小 */
  :deep(.v-btn) {
    min-height: 44px !important;
    min-width: 44px !important;
    padding: 8px 16px !important;
    font-size: 0.875rem !important;
  }

  :deep(.v-btn--icon) {
    min-width: 44px !important;
    min-height: 44px !important;
  }

  /* 优化输入框触摸区域 */
  :deep(.v-field) {
    border-radius: 8px !important;
    min-height: 48px !important;
  }

  :deep(.v-text-field .v-field),
  :deep(.v-select .v-field),
  :deep(.v-textarea .v-field) {
    min-height: 48px !important;
  }

  /* 优化开关触摸区域 */
  :deep(.v-switch) {
    min-height: 44px !important;
    padding: 8px 0 !important;
  }

  /* 优化列表项触摸区域 */
  :deep(.v-list-item) {
    min-height: 48px !important;
    padding: 8px 12px !important;
  }

  /* 优化Tab在移动端 */
  :deep(.v-tab) {
    min-height: 44px !important;
    padding: 0 16px !important;
    font-size: 0.875rem !important;
  }

  /* 主分类标签在移动端优化 */
  :deep(.main-category-tabs .v-tab) {
    min-height: 44px !important;
    padding: 8px 12px !important;
    font-size: 0.8rem !important;
    margin: 2px 1px !important;
  }

  /* 子标签页在移动端优化 */
  :deep(.sub-category-tabs .v-tab) {
    min-height: 44px !important;
    padding: 8px 14px !important;
    font-size: 0.8rem !important;
    margin: 0 3px !important;
  }

  :deep(.sub-category-tabs) {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    padding: 6px 8px 0 8px !important;
  }

  /* 禁用 v-window 的触摸滑动 */
  :deep(.v-window) {
    touch-action: pan-y !important;
  }

  :deep(.v-window__container) {
    touch-action: pan-y !important;
  }

  /* 配置项区域禁用触摸滑动 */
  :deep(.v-card-text),
  :deep(.v-row),
  :deep(.v-col),
  :deep(.v-field),
  :deep(.v-switch),
  :deep(.v-btn),
  :deep(.v-select),
  :deep(.v-text-field),
  :deep(.v-textarea) {
    touch-action: manipulation !important;
  }

  /* 优化对话框在移动端 - 镜面效果 */
  :deep(.v-dialog > .v-card) {
    margin: 16px !important;
    max-height: calc(100vh - 32px) !important;
    border-radius: 20px !important;
    background: rgba(255, 255, 255, 0.85) !important;
    backdrop-filter: blur(25px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(25px) saturate(180%) !important;
    border: 1px solid rgba(255, 255, 255, 0.4) !important;
    box-shadow:
      0 12px 48px rgba(91, 207, 250, 0.3),
      0 4px 16px rgba(245, 171, 185, 0.25),
      inset 0 1px 0 rgba(var(--v-theme-on-surface), 0.1) !important;
  }

  :deep(.v-dialog) {
    max-width: calc(100vw - 32px) !important;
  }

  /* 路径输入组优化 */
  .path-group {
    padding: 10px !important;
    border-radius: 8px !important;
  }

  /* 优化警告框 */
  :deep(.v-alert) {
    border-radius: 10px !important;
    padding: 12px !important;
  }

  /* 优化卡片间距 */
  :deep(.v-card-text) {
    padding: 12px !important;
  }

  /* 优化芯片 */
  :deep(.v-chip) {
    font-size: 0.75rem !important;
    height: 28px !important;
    padding: 0 10px !important;
    min-height: 28px !important;
  }

  /* 优化图标大小 */
  :deep(.v-icon) {
    font-size: 20px !important;
  }

  :deep(.v-icon--size-small) {
    font-size: 18px !important;
  }

  /* 优化行和列的间距 */
  :deep(.v-row) {
    margin: -4px !important;
  }

  :deep(.v-row > .v-col) {
    padding: 6px !important;
  }

  .cache-card {
    min-height: auto;
    height: auto;
  }
}

/* 移动端小屏幕优化 (max-width: 600px) */
@media (max-width: 600px) {
  .plugin-config {
    padding: 6px;
    width: 100%;
    max-width: 100vw;
    box-sizing: border-box;
  }

  .plugin-config :deep(.v-card) {
    border-radius: 10px !important;
  }

  .config-card {
    border-radius: 8px !important;
    margin-bottom: 10px !important;
  }

  /* 移动端标签页优化 */
  :deep(.main-category-tabs .v-tab) {
    padding: 8px 10px !important;
    font-size: 0.75rem !important;
  }

  :deep(.sub-category-tabs .v-tab) {
    padding: 8px 12px !important;
    font-size: 0.75rem !important;
  }

  /* 对话框在小屏幕上全屏 - 保持镜面效果 */
  :deep(.v-dialog) {
    max-width: 100vw !important;
  }

  :deep(.v-dialog > .v-card) {
    margin: 0 !important;
    border-radius: 0 !important;
    max-height: 100vh !important;
    background: rgba(255, 255, 255, 0.9) !important;
    backdrop-filter: blur(30px) saturate(180%) !important;
    -webkit-backdrop-filter: blur(30px) saturate(180%) !important;
  }

  /* 进一步优化间距 */
  :deep(.v-card-text) {
    padding: 10px !important;
  }

  :deep(.v-card-title) {
    padding: 10px !important;
  }
}

/* 桌面端保持固定高度 */
@media (min-width: 960px) {
  .cache-card {
    height: 200px;
  }
}
</style>
