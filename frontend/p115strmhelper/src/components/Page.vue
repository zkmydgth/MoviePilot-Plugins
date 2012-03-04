<template>
  <div class="plugin-page">
    <v-card flat class="rounded border" style="display: flex; flex-direction: column; max-height: 85vh;">
      <!-- 标题区域 -->
      <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-gradient">
        <v-icon icon="mdi-file-link" class="mr-2" color="primary" size="small" />
        <span>115网盘STRM助手</span>
      </v-card-title>

      <!-- 通知区域 -->
      <v-card-text class="px-3 py-1" style="flex-grow: 1; overflow-y: auto; padding-bottom: 48px;">
        <v-alert v-if="error" type="error" density="compact" class="mb-2" variant="tonal" closable>{{ error }}</v-alert>
        <v-alert v-if="actionMessage" :type="actionMessageType" density="compact" class="mb-2" variant="tonal"
          closable>{{ actionMessage }}</v-alert>

        <v-skeleton-loader v-if="loading && !initialDataLoaded" type="article, actions"></v-skeleton-loader>

        <div v-if="initialDataLoaded" class="my-1">
          <!-- 状态和功能区 -->
          <v-row>
            <v-col cols="12" md="6">
              <!-- 基础状态 -->
              <v-card flat class="rounded mb-3 border config-card">
                <v-card-title class="text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient">
                  <v-icon icon="mdi-information" class="mr-2" color="primary" size="small" />
                  <span>系统状态</span>
                </v-card-title>
                <v-card-text class="pa-0">
                  <v-list class="bg-transparent pa-0">
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="status.enabled ? 'success' : 'grey'" icon="mdi-power" size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">插件状态</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="status.enabled ? 'success' : 'grey'" size="x-small" variant="tonal">
                          {{ status.enabled ? '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="status.has_client && initialConfig?.cookies ? 'success' : 'error'"
                          icon="mdi-account-check" size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">115客户端状态</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="status.has_client && initialConfig?.cookies ? 'success' : 'error'"
                          size="x-small" variant="tonal">
                          {{ status.has_client && initialConfig?.cookies ? '已连接' : '未连接' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="status.running ? 'warning' : 'success'" icon="mdi-play-circle" size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">任务状态</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="status.running ? 'warning' : 'success'" size="x-small" variant="tonal">
                          {{ status.running ? '运行中' : '空闲' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                  </v-list>
                </v-card-text>
              </v-card>

              <!-- 账户信息 -->
              <v-card flat class="rounded mb-3 border config-card">
                <v-card-title class="text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient">
                  <v-icon icon="mdi-account-box" class="mr-2" color="primary" size="small" />
                  <span>115账户信息</span>
                </v-card-title>
                <v-card-text class="pa-0">
                  <v-skeleton-loader v-if="userInfo.loading || storageInfo.loading"
                    type="list-item-avatar-three-line, list-item-three-line"></v-skeleton-loader>
                  <div v-else>
                    <v-alert v-if="userInfo.error || storageInfo.error" type="warning" density="compact" class="ma-2"
                      variant="tonal">
                      {{ userInfo.error || storageInfo.error }}
                    </v-alert>
                    <v-list v-else class="bg-transparent pa-0">
                      <!-- 用户名和头像 -->
                      <v-list-item class="px-3 py-1">
                        <template v-slot:prepend>
                          <v-avatar size="32" class="mr-2">
                            <v-img :src="userInfo.avatar" :alt="userInfo.name" v-if="userInfo.avatar"></v-img>
                            <v-icon icon="mdi-account-circle" v-else></v-icon>
                          </v-avatar>
                        </template>
                        <v-list-item-title class="text-body-1 font-weight-medium">{{ userInfo.name || '未知用户'
                          }}</v-list-item-title>
                      </v-list-item>
                      <v-divider class="my-0"></v-divider>
                      <!-- VIP 信息 -->
                      <v-list-item class="px-3 py-1">
                        <template v-slot:prepend>
                          <v-icon :color="userInfo.is_vip ? 'amber-darken-2' : 'grey'" icon="mdi-shield-crown"
                            size="small" />
                        </template>
                        <v-list-item-title class="text-body-2">VIP状态</v-list-item-title>
                        <template v-slot:append>
                          <v-chip :color="userInfo.is_vip ? 'success' : 'grey'" size="x-small" variant="tonal">
                            {{ userInfo.is_vip ? (userInfo.is_forever_vip ? '永久VIP' : `VIP (至 ${userInfo.vip_expire_date
                              || 'N/A'})`) : '非VIP' }}
                          </v-chip>
                        </template>
                      </v-list-item>
                      <v-divider class="my-0"></v-divider>
                      <!-- 存储空间 -->
                      <v-list-item class="px-3 py-1">
                        <v-list-item-title class="text-body-2 mb-1">存储空间</v-list-item-title>
                        <v-list-item-subtitle v-if="storageInfo.used && storageInfo.total" class="text-caption">
                          已用 {{ storageInfo.used }} / 总共 {{ storageInfo.total }} (剩余 {{ storageInfo.remaining }})
                        </v-list-item-subtitle>
                        <v-progress-linear v-if="storageInfo.used && storageInfo.total"
                          :model-value="calculateStoragePercentage(storageInfo.used, storageInfo.total)" color="primary"
                          height="6" rounded class="mt-1"></v-progress-linear>
                        <v-list-item-subtitle v-else class="text-caption text-grey">
                          存储信息不可用
                        </v-list-item-subtitle>
                      </v-list-item>
                    </v-list>
                  </div>
                </v-card-text>
              </v-card>
              <!-- 功能状态 -->
              <v-card flat class="rounded mb-3 border config-card">
                <v-card-title class="text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient">
                  <v-icon icon="mdi-puzzle" class="mr-2" color="primary" size="small" />
                  <span>功能配置</span>
                </v-card-title>
                <v-card-text class="pa-0">
                  <v-list class="bg-transparent pa-0">
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="initialConfig?.transfer_monitor_enabled ? 'success' : 'grey'"
                          icon="mdi-file-move" size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">监控MP整理</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="initialConfig?.transfer_monitor_enabled ? 'success' : 'grey'" size="x-small"
                          variant="tonal">
                          {{ initialConfig?.transfer_monitor_enabled ? '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="initialConfig?.timing_full_sync_strm ? 'success' : 'grey'" icon="mdi-sync"
                          size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">定期全量同步</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="initialConfig?.timing_full_sync_strm ? 'success' : 'grey'" size="x-small"
                          variant="tonal">
                          {{ initialConfig?.timing_full_sync_strm ? '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="initialConfig?.increment_sync_strm_enabled ? 'success' : 'grey'"
                          icon="mdi-book-sync" size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">定期增量同步</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="initialConfig?.increment_sync_strm_enabled ? 'success' : 'grey'" size="x-small"
                          variant="tonal">
                          {{ initialConfig?.increment_sync_strm_enabled ? '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="initialConfig?.monitor_life_enabled ? 'success' : 'grey'"
                          icon="mdi-calendar-heart" size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">监控115生活事件</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="initialConfig?.monitor_life_enabled ? 'success' : 'grey'" size="x-small"
                          variant="tonal">
                          {{ initialConfig?.monitor_life_enabled ? '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="initialConfig?.pan_transfer_enabled ? 'success' : 'grey'" icon="mdi-transfer"
                          size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">网盘整理</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="initialConfig?.pan_transfer_enabled ? 'success' : 'grey'" size="x-small"
                          variant="tonal">
                          {{ initialConfig?.pan_transfer_enabled ? '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon
                          :color="initialConfig?.clear_recyclebin_enabled || initialConfig?.clear_receive_path_enabled ? 'success' : 'grey'"
                          icon="mdi-broom" size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">定期清理</v-list-item-title>
                      <template v-slot:append>
                        <v-chip
                          :color="initialConfig?.clear_recyclebin_enabled || initialConfig?.clear_receive_path_enabled ? 'success' : 'grey'"
                          size="x-small" variant="tonal">
                          {{ initialConfig?.clear_recyclebin_enabled || initialConfig?.clear_receive_path_enabled ?
                            '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                    <v-list-item class="px-3 py-0" style="min-height: 34px;">
                      <template v-slot:prepend>
                        <v-icon :color="initialConfig?.sync_del_enabled ? 'warning' : 'grey'" icon="mdi-delete-sweep"
                          size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">同步删除</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="initialConfig?.sync_del_enabled ? 'warning' : 'grey'" size="x-small"
                          variant="tonal">
                          {{ initialConfig?.sync_del_enabled ? '已启用' : '已禁用' }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0"></v-divider>
                  </v-list>
                </v-card-text>
              </v-card>

              <!-- 同步删除历史记录 -->
              <v-card v-if="initialConfig?.sync_del_enabled" flat class="rounded mb-3 border config-card">
                <v-card-title class="text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient">
                  <v-icon icon="mdi-history" class="mr-2" color="primary" size="small" />
                  <span>同步删除历史</span>
                  <v-spacer></v-spacer>
                  <v-btn v-if="syncDelHistory.length > 6" size="x-small" variant="text"
                    @click="syncDelHistoryDialog.show = true" prepend-icon="mdi-open-in-new">
                    查看全部 ({{ syncDelHistory.length }})
                  </v-btn>
                  <v-btn icon size="x-small" variant="text" @click="loadSyncDelHistory" :loading="syncDelHistoryLoading"
                    class="ml-1">
                    <v-icon>mdi-refresh</v-icon>
                  </v-btn>
                </v-card-title>
                <v-card-text class="pa-0">
                  <v-skeleton-loader v-if="syncDelHistoryLoading"
                    type="list-item-avatar-three-line@3"></v-skeleton-loader>
                  <div v-else-if="syncDelHistory.length === 0" class="text-center py-4">
                    <v-icon icon="mdi-information-outline" size="large" color="grey" class="mb-2"></v-icon>
                    <div class="text-caption text-grey">暂无删除历史</div>
                  </div>
                  <v-list v-else class="bg-transparent pa-0">
                    <template v-for="(item, index) in displayedSyncDelHistory" :key="item.unique || index">
                      <v-list-item class="px-3 py-2">
                        <template v-slot:prepend>
                          <v-avatar size="48" rounded class="mr-3">
                            <v-img :src="item.image" :alt="item.title" cover v-if="item.image"></v-img>
                            <v-icon icon="mdi-movie" v-else></v-icon>
                          </v-avatar>
                        </template>
                        <v-list-item-title class="text-body-2 font-weight-medium">{{ item.title }}</v-list-item-title>
                        <v-list-item-subtitle class="text-caption">
                          <div class="d-flex flex-wrap align-center gap-1 mt-1">
                            <v-chip size="x-small" variant="tonal" color="primary">{{ item.type }}</v-chip>
                            <span v-if="item.year" class="text-grey">· {{ item.year }}</span>
                            <span v-if="item.season" class="text-grey">· S{{ String(item.season).padStart(2, '0')
                            }}</span>
                            <span v-if="item.episode" class="text-grey">· E{{ String(item.episode).padStart(2, '0')
                            }}</span>
                          </div>
                          <div class="text-grey mt-1">{{ item.del_time }}</div>
                        </v-list-item-subtitle>
                        <template v-slot:append>
                          <v-btn icon size="x-small" variant="text" color="error"
                            @click="deleteSyncDelHistory(item.unique)" :loading="deletingHistoryId === item.unique">
                            <v-icon>mdi-delete</v-icon>
                          </v-btn>
                        </template>
                      </v-list-item>
                      <v-divider v-if="index < displayedSyncDelHistory.length - 1" class="my-0"></v-divider>
                    </template>
                  </v-list>
                </v-card-text>
              </v-card>
            </v-col>



            <v-col cols="12" md="6">
              <!-- 路径状态 -->
              <v-card flat class="rounded mb-3 border config-card">
                <v-card-title class="text-subtitle-2 d-flex align-center px-3 py-1 bg-primary-gradient">
                  <v-icon icon="mdi-folder-search" class="mr-2" color="primary" size="small" />
                  <span>路径配置</span>
                </v-card-title>
                <v-card-text class="pa-0">
                  <v-list class="bg-transparent pa-0">
                    <!-- 监控MP整理路径 -->
                    <v-list-item v-if="initialConfig?.transfer_monitor_enabled" class="px-3 py-2">
                      <div>
                        <div class="text-body-2 font-weight-medium mb-1">监控MP整理路径</div>
                        <template v-if="getPathsCount(initialConfig?.transfer_monitor_paths) > 0">
                          <template v-for="(path, index) in getParsedPaths(initialConfig?.transfer_monitor_paths)"
                            :key="`transfer-${index}`">
                            <v-divider v-if="index > 0" class="my-1"></v-divider>
                            <div class="path-mapping-item pa-2 border rounded-sm"
                              style="background-color: rgba(var(--v-theme-on-surface), 0.02);">
                              <v-row dense align="center">
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="primary"
                                    class="mr-2 flex-shrink-0">mdi-folder-home</v-icon>
                                  <div class="text-truncate w-100" :title="path.local">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">本地目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.local || '-' }}</span>
                                  </div>
                                </v-col>
                                <v-col cols="12" sm="2" class="text-center my-1 my-sm-0">
                                  <v-icon color="primary" class="icon-spin-animation">mdi-sync</v-icon>
                                </v-col>
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="success" class="mr-2 flex-shrink-0">mdi-cloud</v-icon>
                                  <div class="text-truncate w-100" :title="path.remote">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">网盘目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.remote || '-' }}</span>
                                  </div>
                                </v-col>
                              </v-row>
                            </div>
                          </template>
                        </template>
                        <template v-else>
                          <div class="text-caption text-error mt-1">未配置路径</div>
                        </template>
                      </div>
                    </v-list-item>

                    <v-divider
                      v-if="initialConfig?.transfer_monitor_enabled && (initialConfig?.increment_sync_strm_enabled || initialConfig?.timing_full_sync_strm || initialConfig?.monitor_life_enabled || initialConfig?.pan_transfer_enabled || initialConfig?.directory_upload_enabled)"
                      class="my-0"></v-divider>

                    <!-- 全量同步路径 -->
                    <v-list-item v-if="initialConfig?.timing_full_sync_strm" class="px-3 py-2">
                      <div>
                        <div class="text-body-2 font-weight-medium mb-1">全量同步路径</div>
                        <template v-if="getPathsCount(initialConfig?.full_sync_strm_paths) > 0">
                          <template v-for="(path, index) in getParsedPaths(initialConfig?.full_sync_strm_paths)"
                            :key="`fullsync-${index}`">
                            <v-divider v-if="index > 0" class="my-1"></v-divider>
                            <div class="path-mapping-item pa-2 border rounded-sm"
                              style="background-color: rgba(var(--v-theme-on-surface), 0.02);">
                              <v-row dense align="center">
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="primary"
                                    class="mr-2 flex-shrink-0">mdi-folder-home</v-icon>
                                  <div class="text-truncate w-100" :title="path.local">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">本地目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.local || '-' }}</span>
                                  </div>
                                </v-col>
                                <v-col cols="12" sm="2" class="text-center my-1 my-sm-0">
                                  <v-icon color="primary" class="icon-spin-animation">mdi-sync</v-icon>
                                </v-col>
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="success" class="mr-2 flex-shrink-0">mdi-cloud</v-icon>
                                  <div class="text-truncate w-100" :title="path.remote">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">网盘目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.remote || '-' }}</span>
                                  </div>
                                </v-col>
                              </v-row>
                            </div>
                          </template>
                        </template>
                        <template v-else>
                          <div class="text-caption text-error mt-1">未配置路径</div>
                        </template>
                      </div>
                    </v-list-item>

                    <v-divider
                      v-if="initialConfig?.timing_full_sync_strm && (initialConfig?.increment_sync_strm_enabled || initialConfig?.monitor_life_enabled || initialConfig?.pan_transfer_enabled || initialConfig?.directory_upload_enabled)"
                      class="my-0"></v-divider>

                    <!-- 增量同步路径 -->
                    <v-list-item v-if="initialConfig?.increment_sync_strm_enabled" class="px-3 py-2">
                      <div>
                        <div class="text-body-2 font-weight-medium mb-1">增量同步路径</div>
                        <template v-if="getPathsCount(initialConfig?.increment_sync_strm_paths) > 0">
                          <template v-for="(path, index) in getParsedPaths(initialConfig?.increment_sync_strm_paths)"
                            :key="`incsync-${index}`">
                            <v-divider v-if="index > 0" class="my-1"></v-divider>
                            <div class="path-mapping-item pa-2 border rounded-sm"
                              style="background-color: rgba(var(--v-theme-on-surface), 0.02);">
                              <v-row dense align="center">
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="primary"
                                    class="mr-2 flex-shrink-0">mdi-folder-home</v-icon>
                                  <div class="text-truncate w-100" :title="path.local">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">本地目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.local || '-' }}</span>
                                  </div>
                                </v-col>
                                <v-col cols="12" sm="2" class="text-center my-1 my-sm-0">
                                  <v-icon color="primary" class="icon-spin-animation">mdi-sync</v-icon>
                                </v-col>
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="success" class="mr-2 flex-shrink-0">mdi-cloud</v-icon>
                                  <div class="text-truncate w-100" :title="path.remote">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">网盘目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.remote || '-' }}</span>
                                  </div>
                                </v-col>
                              </v-row>
                            </div>
                          </template>
                        </template>
                        <template v-else>
                          <div class="text-caption text-error mt-1">未配置路径</div>
                        </template>
                      </div>
                    </v-list-item>

                    <v-divider
                      v-if="initialConfig?.increment_sync_strm_enabled && (initialConfig?.monitor_life_enabled || initialConfig?.pan_transfer_enabled || initialConfig?.directory_upload_enabled)"
                      class="my-0"></v-divider>

                    <!-- 监控115生活事件路径 -->
                    <v-list-item v-if="initialConfig?.monitor_life_enabled" class="px-3 py-2">
                      <div>
                        <div class="text-body-2 font-weight-medium mb-1">监控115生活事件路径</div>
                        <template v-if="getPathsCount(initialConfig?.monitor_life_paths) > 0">
                          <template v-for="(path, index) in getParsedPaths(initialConfig?.monitor_life_paths)"
                            :key="`life-${index}`">
                            <v-divider v-if="index > 0" class="my-1"></v-divider>
                            <div class="path-mapping-item pa-2 border rounded-sm"
                              style="background-color: rgba(var(--v-theme-on-surface), 0.02);">
                              <v-row dense align="center">
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="primary"
                                    class="mr-2 flex-shrink-0">mdi-folder-home</v-icon>
                                  <div class="text-truncate w-100" :title="path.local">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">本地目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.local || '-' }}</span>
                                  </div>
                                </v-col>
                                <v-col cols="12" sm="2" class="text-center my-1 my-sm-0">
                                  <v-icon color="primary" class="icon-spin-animation">mdi-sync</v-icon>
                                </v-col>
                                <v-col cols="12" sm="5" class="d-flex align-center">
                                  <v-icon size="small" color="success" class="mr-2 flex-shrink-0">mdi-cloud</v-icon>
                                  <div class="text-truncate w-100" :title="path.remote">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height: 1.2;">网盘目录</span>
                                    <span class="text-caption" style="line-height: 1.2;">{{ path.remote || '-' }}</span>
                                  </div>
                                </v-col>
                              </v-row>
                            </div>
                          </template>
                        </template>
                        <template v-else>
                          <div class="text-caption text-error mt-1">未配置路径</div>
                        </template>
                      </div>
                    </v-list-item>

                    <v-divider
                      v-if="initialConfig?.monitor_life_enabled && (initialConfig?.pan_transfer_enabled || initialConfig?.directory_upload_enabled)"
                      class="my-0"></v-divider>

                    <!-- 网盘整理目录 -->
                    <v-list-item v-if="initialConfig?.pan_transfer_enabled" class="px-3 py-2">
                      <div>
                        <div class="text-body-2 font-weight-medium mb-1">网盘整理目录</div>
                        <template v-if="getPanTransferPathsCount(initialConfig?.pan_transfer_paths) > 0">
                          <template
                            v-for="(pathItem, index) in getParsedPanTransferPaths(initialConfig?.pan_transfer_paths)"
                            :key="`pan-${index}`">
                            <v-divider v-if="index > 0" class="my-1"></v-divider>
                            <div class="path-item pa-2 border rounded-sm"
                              style="background-color: rgba(var(--v-theme-on-surface), 0.02);">
                              <div class="d-flex align-center">
                                <v-icon size="small" color="success"
                                  class="mr-2 flex-shrink-0">mdi-folder-arrow-down</v-icon>
                                <div class="text-truncate w-100" :title="pathItem.path">
                                  <span class="text-caption font-weight-medium d-block"
                                    style="line-height: 1.2;">待整理网盘目录</span>
                                  <span class="text-caption" style="line-height: 1.2;">{{ pathItem.path }}</span>
                                </div>
                              </div>
                            </div>
                          </template>
                        </template>
                        <template v-else>
                          <div class="text-caption text-error mt-1">未配置网盘整理目录</div>
                        </template>
                      </div>
                    </v-list-item>

                    <v-divider v-if="initialConfig?.pan_transfer_enabled && initialConfig?.directory_upload_enabled"
                      class="my-0"></v-divider>

                    <!-- Directory Upload Paths Display -->
                    <v-list-item v-if="initialConfig?.directory_upload_enabled" class="px-3 py-2">
                      <div>
                        <div class="text-body-2 font-weight-medium mb-1">目录上传路径</div>
                        <template
                          v-if="initialConfig?.directory_upload_path && initialConfig.directory_upload_path.length > 0">
                          <template v-for="(pathGroup, groupIndex) in initialConfig.directory_upload_path"
                            :key="`upload-group-${groupIndex}`">
                            <v-divider v-if="groupIndex > 0" class="my-1"></v-divider>
                            <div class="path-group-item pa-2 border rounded-sm"
                              style="background-color: rgba(var(--v-theme-on-surface), 0.02);">
                              <v-row dense align="center">
                                <v-col cols="12" md="5" class="d-flex align-center">
                                  <v-icon size="small" color="primary"
                                    class="mr-2 flex-shrink-0">mdi-folder-table</v-icon>
                                  <div class="text-truncate w-100" :title="pathGroup.src">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height:1.2;">本地监控</span>
                                    <span class="text-caption" style="line-height:1.2;">{{ pathGroup.src || '-'
                                      }}</span>
                                  </div>
                                </v-col>
                                <v-col cols="12" md="2" class="text-center my-1 my-md-0">
                                  <v-icon color="primary" class="icon-spin-animation">mdi-sync</v-icon>
                                </v-col>
                                <v-col cols="12" md="5" class="d-flex align-center">
                                  <v-icon size="small" color="success"
                                    class="mr-2 flex-shrink-0">mdi-cloud-upload</v-icon>
                                  <div class="text-truncate w-100" :title="pathGroup.dest_remote">
                                    <span class="text-caption font-weight-medium d-block"
                                      style="line-height:1.2;">网盘上传</span>
                                    <span class="text-caption" style="line-height:1.2;">{{ pathGroup.dest_remote || '-'
                                      }}</span>
                                  </div>
                                </v-col>
                              </v-row>
                              <div v-if="pathGroup.dest_local || typeof pathGroup.delete === 'boolean'"
                                class="mt-1 pt-1" style="border-top: 1px dashed rgba(var(--v-border-color), 0.2);">
                                <v-row dense align="center" class="mt-1">
                                  <v-col cols="12" :md="typeof pathGroup.delete === 'boolean' ? 7 : 12">
                                    <div v-if="pathGroup.dest_local" class="d-flex align-center">
                                      <v-icon size="small" color="warning"
                                        class="mr-2 flex-shrink-0">mdi-content-copy</v-icon>
                                      <div class="text-truncate w-100" :title="pathGroup.dest_local">
                                        <span class="text-caption font-weight-medium d-block"
                                          style="line-height:1.2;">本地复制</span>
                                        <span class="text-caption" style="line-height:1.2;">{{ pathGroup.dest_local
                                          }}</span>
                                      </div>
                                    </div>
                                  </v-col>
                                  <v-col v-if="typeof pathGroup.delete === 'boolean'" cols="12"
                                    :md="pathGroup.dest_local ? 5 : 12" class="d-flex align-center"
                                    :class="{ 'justify-md-end': pathGroup.dest_local, 'mt-1 mt-md-0': pathGroup.dest_local }">
                                    <v-icon size="small" :color="pathGroup.delete ? 'error' : 'grey-darken-1'"
                                      class="mr-1 flex-shrink-0">
                                      {{ pathGroup.delete ? 'mdi-delete' : 'mdi-delete-off' }}
                                    </v-icon>
                                    <span class="text-caption"
                                      :class="pathGroup.delete ? 'text-error' : 'text-grey-darken-1'">
                                      {{ pathGroup.delete ? '删除源' : '不删源' }}
                                    </span>
                                  </v-col>
                                </v-row>
                              </div>
                            </div>
                          </template>
                        </template>
                        <template v-else>
                          <div class="text-caption text-error mt-1">未配置路径</div>
                        </template>
                      </div>
                    </v-list-item>
                  </v-list>
                </v-card-text>
              </v-card>

              <!-- 配置提示卡片 -->
              <v-card v-if="!status.has_client || !initialConfig.cookies" flat class="rounded mb-3 border config-card">
                <v-card-text class="pa-3">
                  <div class="d-flex">
                    <v-icon icon="mdi-alert-circle" color="error" class="mr-2" size="small"></v-icon>
                    <div class="text-body-2">
                      <p class="mb-1"><strong>未配置115 Cookie或Cookie无效</strong></p>
                      <p class="mb-0">请在配置页面中设置有效的115网盘Cookie，可通过扫码登录获取。</p>
                      <v-btn color="primary" variant="text" size="small" class="mt-1 px-2 py-0" @click="emit('switch')">
                        <v-icon size="small" class="mr-1">mdi-cog</v-icon>前往配置
                      </v-btn>
                    </div>
                  </div>
                </v-card-text>
              </v-card>

              <v-card v-else-if="!isProperlyCongifured" flat class="rounded mb-3 border config-card">
                <v-card-text class="pa-3">
                  <div class="d-flex">
                    <v-icon icon="mdi-alert-circle" color="warning" class="mr-2" size="small"></v-icon>
                    <div class="text-body-2">
                      <p class="mb-1"><strong>路径配置不完整</strong></p>
                      <p class="mb-0">您已配置115 Cookie，但部分功能路径未配置。请前往配置页面完善路径设置。</p>
                      <v-btn color="primary" variant="text" size="small" class="mt-1 px-2 py-0" @click="emit('switch')">
                        <v-icon size="small" class="mr-1">mdi-cog</v-icon>前往配置
                      </v-btn>
                    </div>
                  </div>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>

          <!-- 帮助信息卡片 -->
          <v-card flat class="rounded mb-3 border config-card">
            <v-card-text class="d-flex align-center px-3 py-1">
              <v-icon icon="mdi-information" color="info" class="mr-2" size="small"></v-icon>
              <span class="text-body-2">
                点击"配置"按钮进行设置，"全量同步"和"分享同步"按钮可立即执行相应任务。
              </span>
            </v-card-text>
          </v-card>
        </div>
      </v-card-text>

      <v-divider></v-divider>

      <v-card-actions class="px-2 py-1 sticky-actions d-flex justify-space-between align-center"
        style="flex-shrink: 0; gap: 8px;">
        <v-btn color="info" @click="refreshStatus" prepend-icon="mdi-refresh" :disabled="refreshing"
          :loading="refreshing" variant="text" size="small">刷新状态</v-btn>

        <div class="d-flex align-center" style="gap: 4px;">
          <!-- “更多操作” 菜单 -->
          <v-menu offset-y>
            <template v-slot:activator="{ props }">
              <v-btn v-bind="props" variant="text" size="small" append-icon="mdi-dots-vertical">
                更多
              </v-btn>
            </template>
            <v-list density="compact">
              <v-list-item @click="fullSyncConfirmDialog = true"
                :disabled="!status.enabled || !status.has_client || actionLoading">
                <template v-slot:prepend>
                  <v-icon color="warning">mdi-sync</v-icon>
                </template>
                <v-list-item-title>全量同步</v-list-item-title>
              </v-list-item>
              <v-list-item @click="fullSyncDbConfirmDialog = true"
                :disabled="!status.enabled || !status.has_client || actionLoading">
                <template v-slot:prepend>
                  <v-icon color="primary">mdi-database-sync</v-icon>
                </template>
                <v-list-item-title>全量同步数据库</v-list-item-title>
              </v-list-item>
              <v-list-item @click="openShareDialog" :disabled="!status.enabled || !status.has_client || actionLoading"
                :loading="shareSyncLoading">
                <template v-slot:prepend>
                  <v-icon color="info">mdi-share-variant</v-icon>
                </template>
                <v-list-item-title>分享同步</v-list-item-title>
              </v-list-item>
              <v-list-item @click="openOfflineDownloadDialog"
                :disabled="!status.enabled || !status.has_client || actionLoading">
                <template v-slot:prepend>
                  <v-icon color="secondary">mdi-cloud-download-outline</v-icon>
                </template>
                <v-list-item-title>离线下载</v-list-item-title>
              </v-list-item>
            </v-list>
          </v-menu>

          <!-- 始终可见的核心按钮 -->
          <v-btn color="primary" @click="emit('switch')" prepend-icon="mdi-cog" variant="text" size="small">配置</v-btn>
          <v-btn color="error" @click="emit('close')" variant="flat" size="small" class="custom-close-btn"
            aria-label="关闭"
            style="min-width: auto !important; padding: 0 10px !important; height: 28px !important; line-height: 28px !important;">
            <v-icon size="small">mdi-close</v-icon>
          </v-btn>
        </div>
      </v-card-actions>
    </v-card>
  </div>

  <!-- 分享同步对话框 -->
  <v-dialog v-model="shareDialog.show" max-width="900" scrollable>
    <v-card>
      <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5">
        <v-icon icon="mdi-share-variant" class="mr-2" color="primary" size="small" />
        <span>115网盘分享同步</span>
        <v-spacer></v-spacer>
        <v-btn icon="mdi-close" variant="text" size="small" @click="closeShareDialog"></v-btn>
      </v-card-title>

      <v-card-text class="px-3 py-2">
        <v-alert v-if="shareDialog.error" type="error" density="compact" class="mb-3" variant="tonal">
          {{ shareDialog.error }}
        </v-alert>

        <!-- 全局配置 -->
        <v-card variant="outlined" class="mb-4">
          <v-card-title class="text-subtitle-2 px-3 py-2 bg-grey-lighten-4">
            <v-icon icon="mdi-cog" size="small" class="mr-2"></v-icon>
            全局配置
          </v-card-title>
          <v-card-text class="pa-3">
            <v-row>
              <v-col cols="12">
                <v-select v-model="shareDialog.globalMediaservers" label="刷新媒体服务器" hint="应用于所有分享配置的媒体服务器刷新设置"
                  persistent-hint :items="mediaservers" multiple chips closable-chips variant="outlined"
                  density="compact"></v-select>
              </v-col>
            </v-row>
            <v-row class="mt-2">
              <v-col cols="12">
                <v-textarea v-model="shareDialog.globalMpMediaserverPaths" label="MP-媒体库 目录转换"
                  hint="格式：媒体库路径#MP路径，多个用换行分隔。应用于所有分享配置" persistent-hint variant="outlined" density="compact" rows="3"
                  auto-grow placeholder="例如:&#10;/media#/mp&#10;/nas#/movie"></v-textarea>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>

        <!-- 分享配置列表 -->
        <v-card variant="outlined">
          <v-card-title class="text-subtitle-2 px-3 py-2 bg-grey-lighten-4 d-flex align-center">
            <v-icon icon="mdi-share-variant" size="small" class="mr-2"></v-icon>
            <span class="flex-grow-1">分享配置列表</span>
            <v-btn size="small" prepend-icon="mdi-delete-sweep" variant="text" color="error" @click="clearShareConfigs"
              :disabled="shareDialog.configs.length === 0" title="清空所有分享配置">
              清空
            </v-btn>
            <v-btn size="small" prepend-icon="mdi-plus" variant="tonal" color="primary" @click="addShareConfig"
              class="ml-2">
              添加分享
            </v-btn>
          </v-card-title>
          <v-card-text class="pa-0">
            <v-list v-if="shareDialog.configs.length > 0" class="pa-0">
              <template v-for="(config, index) in shareDialog.configs" :key="index">
                <v-list-item class="px-3 py-2">
                  <template v-slot:prepend>
                    <v-icon icon="mdi-share-variant" color="primary" size="small" class="mr-2"></v-icon>
                  </template>
                  <v-list-item-title class="text-body-2 font-weight-medium">
                    {{ config.share_link || config.share_code || `分享配置 ${index + 1}` }}
                  </v-list-item-title>
                  <v-list-item-subtitle class="text-caption mt-1">
                    <div class="d-flex flex-column">
                      <span v-if="config.local_path" class="mb-1">
                        <v-icon icon="mdi-folder" size="x-small" class="mr-1"></v-icon>
                        本地路径: {{ config.local_path }}
                      </span>
                      <span v-else class="text-grey">未配置本地路径</span>
                      <div class="d-flex flex-wrap ga-2 mt-1">
                        <span v-if="config.share_path && config.share_path !== '/'">
                          <v-icon icon="mdi-folder-network" size="x-small" class="mr-1"></v-icon>
                          分享路径: {{ config.share_path }}
                        </span>
                        <span v-if="config.min_file_size">
                          <v-icon icon="mdi-file-size" size="x-small" class="mr-1"></v-icon>
                          最小文件: {{ formatBytes(config.min_file_size) }}
                        </span>
                        <v-chip v-if="config.moviepilot_transfer" size="x-small" color="primary" variant="tonal">
                          MP整理
                        </v-chip>
                      </div>
                    </div>
                  </v-list-item-subtitle>
                  <template v-slot:append>
                    <div class="d-flex align-center">
                      <v-btn icon="mdi-pencil" size="small" variant="text" color="primary"
                        @click="editShareConfig(index)" title="编辑"></v-btn>
                      <v-btn icon="mdi-delete" size="small" variant="text" color="error"
                        @click="removeShareConfig(index)" title="删除"></v-btn>
                    </div>
                  </template>
                </v-list-item>
                <v-divider v-if="index < shareDialog.configs.length - 1" class="my-0"></v-divider>
              </template>
            </v-list>
            <v-alert v-else type="info" density="compact" variant="tonal" class="ma-3">
              <div class="d-flex align-center">
                <span>暂无分享配置，请点击"添加分享"按钮添加</span>
              </div>
            </v-alert>
          </v-card-text>
        </v-card>
      </v-card-text>

      <v-divider></v-divider>
      <v-card-actions class="px-3 py-1">
        <v-btn color="grey" variant="text" @click="closeShareDialog" size="small">取消</v-btn>
        <v-spacer></v-spacer>
        <v-tooltip location="top">
          <template v-slot:activator="{ props }">
            <v-btn color="info" variant="text" @click="saveShareConfigs" :loading="shareConfigSaving" size="small"
              v-bind="props">
              保存配置
            </v-btn>
          </template>
          <span>仅保存配置到系统，不执行同步任务</span>
        </v-tooltip>
        <v-tooltip location="top">
          <template v-slot:activator="{ props }">
            <v-btn color="primary" variant="text" @click="executeShareSync" :loading="shareSyncLoading"
              :disabled="!isShareDialogValid" size="small" class="ml-2" v-bind="props">
              保存并同步
            </v-btn>
          </template>
          <span>保存配置并立即执行分享同步任务（需要至少一个有效配置）</span>
        </v-tooltip>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- 分享配置编辑对话框 -->
  <v-dialog v-model="shareConfigDialog.show" max-width="700" scrollable>
    <v-card>
      <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5">
        <v-icon icon="mdi-share-variant" class="mr-2" color="primary" size="small" />
        <span>{{ shareConfigDialog.editingIndex >= 0 ? '编辑分享配置' : '添加分享配置' }}</span>
        <v-spacer></v-spacer>
        <v-btn icon="mdi-close" variant="text" size="small" @click="closeShareConfigDialog"></v-btn>
      </v-card-title>

      <v-card-text class="px-3 py-3">
        <v-alert v-if="shareConfigDialog.error" type="error" density="compact" class="mb-3" variant="tonal" closable>
          {{ shareConfigDialog.error }}
        </v-alert>

        <v-row class="mb-2">
          <v-col cols="12">
            <v-text-field v-model="shareConfigDialog.shareLink" label="分享链接" hint="115网盘分享链接" persistent-hint
              variant="outlined" density="compact" prepend-inner-icon="mdi-link"></v-text-field>
          </v-col>
        </v-row>

        <v-row class="mb-2">
          <v-col cols="12" md="6">
            <v-text-field v-model="shareConfigDialog.shareCode" label="分享码" hint="分享码，和分享链接选填一项" persistent-hint
              variant="outlined" density="compact" prepend-inner-icon="mdi-key"></v-text-field>
          </v-col>
          <v-col cols="12" md="6">
            <v-text-field v-model="shareConfigDialog.shareReceive" label="分享密码" hint="分享密码，和分享链接选填一项" persistent-hint
              variant="outlined" density="compact" prepend-inner-icon="mdi-lock" type="password"></v-text-field>
          </v-col>
        </v-row>

        <v-row class="mb-2">
          <v-col cols="12" md="6">
            <v-text-field v-model="shareConfigDialog.sharePath" label="分享路径" hint="分享内容列表中的相对路径，默认为根目录 /"
              persistent-hint variant="outlined" density="compact"
              prepend-inner-icon="mdi-folder-network"></v-text-field>
          </v-col>
          <v-col cols="12" md="6">
            <v-text-field v-model="shareConfigDialog.localPath" label="本地路径" hint="本地生成STRM文件的路径" persistent-hint
              variant="outlined" density="compact" prepend-inner-icon="mdi-folder" append-icon="mdi-folder-search"
              @click:append="openShareConfigDirSelector('local')"></v-text-field>
          </v-col>
        </v-row>

        <v-row class="mb-2">
          <v-col cols="12" md="6">
            <v-text-field v-model="shareConfigDialog.minFileSizeFormatted" label="分享生成最小文件大小" hint="小于此值不生成STRM(K,M,G)"
              persistent-hint variant="outlined" density="compact" placeholder="例如: 100M" clearable
              prepend-inner-icon="mdi-file-document"></v-text-field>
          </v-col>
        </v-row>

        <v-divider class="my-3"></v-divider>

        <v-row class="mb-2">
          <v-col cols="12">
            <v-switch v-model="shareConfigDialog.moviepilotTransfer" label="STRM 交由 MoviePilot 整理" color="primary"
              density="compact" hide-details>
              <template v-slot:label>
                <div class="d-flex align-center">
                  <v-icon icon="mdi-transfer" size="small" class="mr-2"></v-icon>
                  <span>STRM 交由 MoviePilot 整理</span>
                </div>
              </template>
            </v-switch>
          </v-col>
        </v-row>

        <v-expand-transition>
          <div v-if="!shareConfigDialog.moviepilotTransfer">
            <v-row class="mb-2">
              <v-col cols="12" md="4" class="d-flex align-center">
                <v-switch v-model="shareConfigDialog.autoDownloadMediainfo" color="primary" density="compact"
                  hide-details class="flex-grow-1">
                  <template v-slot:label>
                    <div class="d-flex align-center">
                      <v-icon icon="mdi-download" size="small" class="mr-2"></v-icon>
                      <span>自动下载网盘元数据</span>
                    </div>
                  </template>
                </v-switch>
              </v-col>
              <v-col cols="12" md="4" class="d-flex align-center">
                <v-switch v-model="shareConfigDialog.mediaServerRefresh" color="primary" density="compact" hide-details
                  class="flex-grow-1">
                  <template v-slot:label>
                    <div class="d-flex align-center">
                      <v-icon icon="mdi-refresh" size="small" class="mr-2"></v-icon>
                      <span>刷新媒体服务器</span>
                    </div>
                  </template>
                </v-switch>
              </v-col>
              <v-col cols="12" md="4" class="d-flex align-center">
                <v-switch v-model="shareConfigDialog.scrapeMetadata" color="primary" density="compact" hide-details
                  class="flex-grow-1">
                  <template v-slot:label>
                    <div class="d-flex align-center">
                      <v-icon icon="mdi-file-search" size="small" class="mr-2"></v-icon>
                      <span>是否刮削元数据</span>
                    </div>
                  </template>
                </v-switch>
              </v-col>
            </v-row>
          </div>
        </v-expand-transition>

        <v-alert type="info" variant="tonal" density="compact" class="mt-3">
          <div class="text-caption">
            <div class="mb-1"><strong>提示：</strong></div>
            <ul class="ma-0 pl-4">
              <li>分享链接/分享码和分享密码只需要二选一配置即可</li>
              <li>同时填写分享链接、分享码和分享密码时，优先读取分享链接</li>
              <li>当 STRM交由MoviePilot整理 关闭时，才能配置自动下载网盘元数据、刷新媒体服务器和是否刮削元数据</li>
            </ul>
          </div>
        </v-alert>
      </v-card-text>

      <v-divider></v-divider>
      <v-card-actions class="px-3 py-1">
        <v-btn color="grey" variant="text" @click="closeShareConfigDialog" size="small">取消</v-btn>
        <v-spacer></v-spacer>
        <v-btn color="primary" variant="text" @click="saveShareConfig" :disabled="!isShareConfigDialogValid"
          size="small">
          保存
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- 离线下载对话框 -->
  <v-dialog v-model="offlineDownloadDialog.show" max-width="800" persistent>
    <v-card>
      <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5">
        <v-icon icon="mdi-cloud-download-outline" class="mr-2" color="primary" size="small" />
        <span>115离线下载</span>
        <v-spacer></v-spacer>
        <v-btn icon="mdi-close" variant="text" size="small" @click="closeOfflineDownloadDialog"></v-btn>
      </v-card-title>

      <v-card-text class="pa-0">
        <v-tabs v-model="offlineDownloadDialog.activeTab" bg-color="primary-gradient" grow>
          <v-tab value="tasks">任务列表</v-tab>
          <v-tab value="add">添加任务</v-tab>
        </v-tabs>

        <v-window v-model="offlineDownloadDialog.activeTab" touchless>
          <!-- 任务列表 -->
          <v-window-item value="tasks">
            <div style="overflow-x: auto;" @touchstart.stop @touchmove.stop @touchend.stop>
              <v-alert v-if="offlineDownloadDialog.error" type="error" density="compact" class="ma-3" variant="tonal">
                {{ offlineDownloadDialog.error }}
              </v-alert>
              <v-data-table-server :headers="offlineDownloadDialog.headers" :items="offlineDownloadDialog.tasks"
                :items-length="offlineDownloadDialog.totalTasks" :loading="offlineDownloadDialog.loading"
                :items-per-page="offlineDownloadDialog.itemsPerPage" @update:options="fetchOfflineTasks"
                density="compact" class="ma-2" no-data-text="没有离线下载任务" loading-text="正在加载任务..."
                items-per-page-text="每页条目数" style="min-width: 700px;" @touchstart.stop @touchmove.stop @touchend.stop>
                <template v-slot:item.progress="{ item }">
                  <div class="d-flex align-center">
                    <v-progress-linear :model-value="item.percent" :color="getTaskStatusColor(item.status)"
                      :stream="item.status === 0 || item.status === 3" :striped="item.status === 0 || item.status === 3"
                      height="8" rounded class="flex-grow-1 mr-3"></v-progress-linear>
                    <div class="text-no-wrap text-caption font-weight-medium"
                      style="min-width: 40px; text-align: right;">
                      {{ item.percent }}%
                    </div>
                  </div>
                </template>
                <template v-slot:item.status_text="{ item }">
                  <v-chip :color="getTaskStatusColor(item.status)" size="x-small" variant="tonal">
                    {{ item.status_text }}
                  </v-chip>
                </template>
              </v-data-table-server>
            </div>
          </v-window-item>

          <!-- 添加任务 -->
          <v-window-item value="add">
            <div class="pa-3">
              <v-alert v-if="offlineDownloadDialog.addError" type="error" density="compact" class="mb-3"
                variant="tonal">
                {{ offlineDownloadDialog.addError }}
              </v-alert>
              <v-textarea v-model="offlineDownloadDialog.links" label="下载链接" hint="每行一个链接，支持 http(s)/ftp/magnet/ed2k"
                persistent-hint variant="outlined" rows="5" clearable></v-textarea>
              <v-text-field v-model="offlineDownloadDialog.destPath" label="网盘保存路径 (可选)" hint="可选，默认为网盘待整理目录"
                persistent-hint variant="outlined" density="compact" class="mt-3"
                append-inner-icon="mdi-folder-network-outline" @click:append-inner="openOfflineDestDirSelector"
                clearable></v-text-field>

              <div class="d-flex justify-end mt-3">
                <v-btn color="primary" @click="addOfflineTask" :loading="offlineDownloadDialog.adding"
                  :disabled="!offlineDownloadDialog.links || offlineDownloadDialog.adding" prepend-icon="mdi-plus">
                  添加任务
                </v-btn>
              </div>
            </div>
          </v-window-item>
        </v-window>
      </v-card-text>
    </v-card>
  </v-dialog>

  <!-- 目录选择器对话框 -->
  <v-dialog v-model="dirDialog.show" max-width="800">
    <v-card>
      <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-1 bg-primary-lighten-5">
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
              @click="navigateToParentDir" class="py-0" style="min-height: auto;">
              <template v-slot:prepend>
                <v-icon icon="mdi-arrow-up" size="small" class="mr-2" color="grey" />
              </template>
              <v-list-item-title class="text-body-2">上级目录</v-list-item-title>
              <v-list-item-subtitle>..</v-list-item-subtitle>
            </v-list-item>

            <v-list-item v-for="(item, index) in dirDialog.items" :key="index" @click="selectDir(item)"
              :disabled="!item.is_dir" class="py-0" style="min-height: auto;">
              <template v-slot:prepend>
                <v-icon :icon="item.is_dir ? 'mdi-folder' : 'mdi-file'" size="small" class="mr-2"
                  :color="item.is_dir ? 'success' : 'blue'" />
              </template>
              <v-list-item-title class="text-body-2">{{ item.name }}</v-list-item-title>
            </v-list-item>

            <v-list-item v-if="!dirDialog.items.length" class="py-2 text-center">
              <v-list-item-title class="text-body-2 text-grey">该目录为空或访问受限</v-list-item-title>
            </v-list-item>
          </v-list>
        </div>

        <!-- 新增：根目录限制提示 -->
        <v-alert v-if="dirDialog.currentPath === '/' && !dirDialog.isLocal" type="warning" density="compact"
          class="mt-2 text-caption" variant="tonal" icon="mdi-alert-circle-outline">
          115离线下载不支持选择根目录，请选择或进入一个子目录。
        </v-alert>

        <v-alert v-if="dirDialog.error" type="error" density="compact" class="mt-2 text-caption" variant="tonal">
          {{ dirDialog.error }}
        </v-alert>
      </v-card-text>

      <v-card-actions class="px-3 py-1">
        <v-spacer></v-spacer>
        <!-- 修改：为 '选择当前目录' 按钮添加禁用条件 -->
        <v-btn color="primary" @click="confirmDirSelection"
          :disabled="!dirDialog.currentPath || dirDialog.loading || (dirDialog.currentPath === '/' && !dirDialog.isLocal)"
          variant="text" size="small">
          选择当前目录
        </v-btn>
        <v-btn color="grey" @click="closeDirDialog" variant="text" size="small">
          取消
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- 全量同步确认对话框 -->
  <v-dialog v-model="fullSyncConfirmDialog" max-width="500" persistent>
    <v-card>
      <v-card-title class="text-h6 d-flex align-center">
        <v-icon icon="mdi-alert-circle-outline" color="warning" class="mr-2"></v-icon>
        确认操作
      </v-card-title>
      <v-card-text>
        <div class="mb-2">您确定要立即执行全量同步吗？</div>
        <v-alert v-if="initialConfig?.full_sync_media_server_refresh_enabled" type="warning" variant="tonal"
          density="compact" class="mt-2" icon="mdi-alert">
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

  <v-dialog v-model="fullSyncDbConfirmDialog" max-width="450" persistent>
    <v-card>
      <v-card-title class="text-h6 d-flex align-center">
        <v-icon icon="mdi-alert-circle-outline" color="warning" class="mr-2"></v-icon>
        确认操作
      </v-card-title>
      <v-card-text>
        您确定要立即执行全量同步数据库吗？该操作会覆盖原有数据库数据
      </v-card-text>
      <v-card-actions>
        <v-spacer></v-spacer>
        <v-btn color="grey" variant="text" @click="fullSyncDbConfirmDialog = false" :disabled="syncDbLoading">
          取消
        </v-btn>
        <v-btn color="warning" variant="text" @click="handleConfirmFullSyncDb" :loading="syncDbLoading">
          确认执行
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <!-- 同步删除历史记录对话框 -->
  <v-dialog v-model="syncDelHistoryDialog.show" max-width="900" scrollable>
    <v-card>
      <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2 bg-primary-gradient">
        <v-icon icon="mdi-history" class="mr-2" color="primary" size="small" />
        <span>同步删除历史记录 (共 {{ syncDelHistory.length }} 条)</span>
        <v-spacer></v-spacer>
        <v-btn icon size="x-small" variant="text" @click="loadSyncDelHistory" :loading="syncDelHistoryLoading">
          <v-icon>mdi-refresh</v-icon>
        </v-btn>
        <v-btn icon size="small" variant="text" @click="syncDelHistoryDialog.show = false">
          <v-icon>mdi-close</v-icon>
        </v-btn>
      </v-card-title>
      <v-card-text class="pa-0 sync-del-history-content">
        <v-skeleton-loader v-if="syncDelHistoryLoading" type="list-item-avatar-three-line@5"></v-skeleton-loader>
        <div v-else-if="syncDelHistory.length === 0" class="text-center py-8">
          <v-icon icon="mdi-information-outline" size="large" color="grey" class="mb-2"></v-icon>
          <div class="text-caption text-grey">暂无删除历史</div>
        </div>
        <v-list v-else class="bg-transparent pa-0">
          <template v-for="(item, index) in syncDelHistory" :key="item.unique || index">
            <v-list-item class="px-3 py-2">
              <template v-slot:prepend>
                <v-avatar size="56" rounded class="mr-3">
                  <v-img :src="item.image" :alt="item.title" cover v-if="item.image"></v-img>
                  <v-icon icon="mdi-movie" v-else></v-icon>
                </v-avatar>
              </template>
              <v-list-item-title class="text-body-1 font-weight-medium">{{ item.title }}</v-list-item-title>
              <v-list-item-subtitle class="text-caption">
                <div class="d-flex flex-wrap align-center gap-1 mt-1">
                  <v-chip size="small" variant="tonal" color="primary">{{ item.type }}</v-chip>
                  <span v-if="item.year" class="text-grey">· {{ item.year }}</span>
                  <span v-if="item.season" class="text-grey">· S{{ String(item.season).padStart(2, '0') }}</span>
                  <span v-if="item.episode" class="text-grey">· E{{ String(item.episode).padStart(2, '0') }}</span>
                </div>
                <div v-if="item.path" class="text-grey mt-1" style="word-break: break-all;">{{ item.path }}</div>
                <div class="text-grey mt-1">{{ item.del_time }}</div>
              </v-list-item-subtitle>
              <template v-slot:append>
                <v-btn icon size="small" variant="text" color="error" @click="deleteSyncDelHistory(item.unique)"
                  :loading="deletingHistoryId === item.unique">
                  <v-icon>mdi-delete</v-icon>
                </v-btn>
              </template>
            </v-list-item>
            <v-divider v-if="index < syncDelHistory.length - 1" class="my-0"></v-divider>
          </template>
        </v-list>
      </v-card-text>
    </v-card>
  </v-dialog>

</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue';

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

const emit = defineEmits(['close', 'switch', 'update:config', 'action']);

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
  if (!+bytes) return '';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'K', 'M', 'G', 'T'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  const formattedNum = parseFloat((bytes / Math.pow(k, i)).toFixed(dm));
  return `${formattedNum} ${sizes[i]}`;
};

// 状态变量
const loading = ref(true);
const refreshing = ref(false);
const syncLoading = ref(false);
const syncDbLoading = ref(false);
const shareSyncLoading = ref(false);
const shareConfigSaving = ref(false);
const initialDataLoaded = ref(false);
const error = ref(null);
const actionMessage = ref(null);
const actionMessageType = ref('info');
const actionLoading = ref(false);
const fullSyncConfirmDialog = ref(false);
const fullSyncDbConfirmDialog = ref(false);
const syncDelHistory = ref([]);
const syncDelHistoryLoading = ref(false);
const deletingHistoryId = ref(null);
let syncDelHistoryRefreshTimer = null;

// 只显示最近6条记录
const displayedSyncDelHistory = computed(() => {
  return syncDelHistory.value.slice(0, 6);
});

// 同步删除历史记录对话框
const syncDelHistoryDialog = reactive({
  show: false
});

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

const offlineDownloadDialog = reactive({
  show: false,
  activeTab: 'tasks',
  loading: false,
  adding: false,
  error: null,
  addError: null,
  tasks: [],
  totalTasks: 0,
  itemsPerPage: 10,
  headers: [
    { title: '文件名', key: 'name', align: 'start', sortable: false, minWidth: '200px' },
    { title: '大小', key: 'size_text', align: 'end', sortable: false, cellProps: { class: 'text-no-wrap' }, minWidth: '80px' },
    { title: '状态', key: 'status_text', align: 'center', sortable: false, minWidth: '80px' },
    { title: '进度', key: 'progress', align: 'start', sortable: false, minWidth: '120px' },
  ],
  links: '',
  destPath: '',
});

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

const isProperlyCongifured = computed(() => {
  if (!props.initialConfig) return false;
  const hasBasicConfig = props.initialConfig.enabled && props.initialConfig.cookies && props.initialConfig.moviepilot_address;
  if (!hasBasicConfig) return false;
  const hasTransferPaths = getPathsCount(props.initialConfig.transfer_monitor_paths) > 0 && props.initialConfig.transfer_monitor_enabled;
  const hasFullSyncPaths = getPathsCount(props.initialConfig.full_sync_strm_paths) > 0 && (props.initialConfig.timing_full_sync_strm);
  const hasIncrementSyncPaths = getPathsCount(props.initialConfig.increment_sync_strm_paths) > 0 && (props.initialConfig.increment_sync_strm_enabled);
  const hasLifePaths = getPathsCount(props.initialConfig.monitor_life_paths) > 0 && props.initialConfig.monitor_life_enabled;
  const hasSharePaths = Array.isArray(props.initialConfig.share_strm_config) && props.initialConfig.share_strm_config.length > 0;
  return hasTransferPaths || hasFullSyncPaths || hasIncrementSyncPaths || hasLifePaths || hasSharePaths;
});

const getPathsCount = (pathString) => {
  if (!pathString) return 0;
  try {
    const paths = pathString.split('\n').filter(line => line.trim() && line.includes('#'));
    return paths.length;
  } catch (e) {
    console.error('解析路径字符串失败:', e);
    return 0;
  }
};

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

const getStatus = async () => {
  loading.value = true;
  error.value = null;
  try {
    const pluginId = "P115StrmHelper";
    const result = await props.api.get(`plugin/${pluginId}/get_status`);
    // 此处 result.data 的访问方式与新模型兼容，无需修改
    if (result && result.code === 0 && result.data) {
      status.enabled = Boolean(result.data.enabled);
      status.has_client = Boolean(result.data.has_client);
      status.running = Boolean(result.data.running);
      try {
        const configData = await props.api.get(`plugin/${pluginId}/get_config`);
        if (configData) {
          Object.assign(props.initialConfig, configData);
          console.log('已获取最新配置:', props.initialConfig);
        }
      } catch (configErr) {
        console.error('获取配置失败:', configErr);
      }
      initialDataLoaded.value = true;
    } else {
      if (props.initialConfig) {
        status.enabled = Boolean(props.initialConfig.enabled);
        status.has_client = Boolean(props.initialConfig.cookies && props.initialConfig.cookies.trim() !== '');
        status.running = false;
        initialDataLoaded.value = true;
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
  setTimeout(() => { actionMessage.value = null; }, 3000);
};

const handleConfirmFullSync = async () => {
  fullSyncConfirmDialog.value = false;
  await triggerFullSync();
};

const handleConfirmFullSyncDb = async () => {
  fullSyncDbConfirmDialog.value = false;
  await triggerFullSyncDb();
};

const triggerFullSync = async () => {
  syncLoading.value = true;
  actionLoading.value = true;
  error.value = null;
  actionMessage.value = null;
  try {
    if (!status.enabled) throw new Error('插件未启用，请先在配置页面启用插件');
    if (!status.has_client) throw new Error('插件未配置Cookie或Cookie无效，请先在配置页面设置115 Cookie');
    if (getPathsCount(props.initialConfig?.full_sync_strm_paths) === 0) throw new Error('未配置全量同步路径，请先在配置页面设置同步路径');
    const pluginId = "P115StrmHelper";
    const result = await props.api.post(`plugin/${pluginId}/full_sync`);
    if (result && result.code === 0) {
      actionMessage.value = result.msg || '全量同步任务已启动';
      actionMessageType.value = 'success';
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

const triggerFullSyncDb = async () => {
  syncDbLoading.value = true;
  actionLoading.value = true;
  error.value = null;
  actionMessage.value = null;
  try {
    if (!status.enabled) throw new Error('插件未启用，请先在配置页面启用插件');
    if (!status.has_client) throw new Error('插件未配置Cookie或Cookie无效，请先在配置页面设置115 Cookie');
    const pluginId = "P115StrmHelper";
    const result = await props.api.post(`plugin/${pluginId}/full_sync_db`);
    if (result && result.code === 0) {
      actionMessage.value = result.msg || '全量同步数据库任务已启动';
      actionMessageType.value = 'success';
      await getStatus();
    } else {
      throw new Error(result?.msg || '启动全量同步数据库失败');
    }
  } catch (err) {
    error.value = `启动全量同步数据库失败: ${err.message || '未知错误'}`;
    console.error('启动全量同步数据库失败:', err);
  } finally {
    syncDbLoading.value = false;
    actionLoading.value = false;
  }
};

const shareDialog = reactive({
  show: false,
  error: null,
  configs: [],
  globalMediaservers: [],
  globalMpMediaserverPaths: '',
});

const shareConfigDialog = reactive({
  show: false,
  error: null,
  editingIndex: -1,
  shareLink: '',
  shareCode: '',
  shareReceive: '',
  sharePath: '/',
  localPath: '',
  minFileSizeFormatted: '',
  moviepilotTransfer: false,
  autoDownloadMediainfo: false,
  mediaServerRefresh: false,
  scrapeMetadata: false,
});

const mediaservers = ref([]);

const isShareDialogValid = computed(() => {
  return shareDialog.configs.length > 0 && shareDialog.configs.every(config => {
    if (!config.local_path) return false;
    if (!config.share_link && !config.share_code) return false;
    if (config.share_code && !config.share_receive) return false;
    return true;
  });
});

const isShareConfigDialogValid = computed(() => {
  if (!shareConfigDialog.localPath) return false;
  if (!shareConfigDialog.shareLink && !shareConfigDialog.shareCode) return false;
  if (shareConfigDialog.shareCode && !shareConfigDialog.shareReceive) return false;
  return true;
});

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

const openShareDialog = () => {
  shareDialog.show = true;
  shareDialog.error = null;
  if (props.initialConfig) {
    // 加载分享配置列表
    shareDialog.configs = Array.isArray(props.initialConfig.share_strm_config)
      ? JSON.parse(JSON.stringify(props.initialConfig.share_strm_config))
      : [];

    // 加载全局配置
    shareDialog.globalMediaservers = Array.isArray(props.initialConfig.share_strm_mediaservers)
      ? [...props.initialConfig.share_strm_mediaservers]
      : [];
    shareDialog.globalMpMediaserverPaths = props.initialConfig.share_strm_mp_mediaserver_paths || '';
  }
};

const closeShareDialog = () => {
  shareDialog.show = false;
  shareDialog.configs = [];
  shareDialog.error = null;
};

const addShareConfig = () => {
  shareConfigDialog.editingIndex = -1;
  shareConfigDialog.shareLink = '';
  shareConfigDialog.shareCode = '';
  shareConfigDialog.shareReceive = '';
  shareConfigDialog.sharePath = '/';
  shareConfigDialog.localPath = '';
  shareConfigDialog.minFileSizeFormatted = '';
  shareConfigDialog.moviepilotTransfer = false;
  shareConfigDialog.autoDownloadMediainfo = false;
  shareConfigDialog.mediaServerRefresh = false;
  shareConfigDialog.scrapeMetadata = false;
  shareConfigDialog.error = null;
  shareConfigDialog.show = true;
};

const editShareConfig = (index) => {
  if (index < 0 || index >= shareDialog.configs.length) return;
  const config = shareDialog.configs[index];
  shareConfigDialog.editingIndex = index;
  shareConfigDialog.shareLink = config.share_link || '';
  shareConfigDialog.shareCode = config.share_code || '';
  shareConfigDialog.shareReceive = config.share_receive || '';
  shareConfigDialog.sharePath = config.share_path || '/';
  shareConfigDialog.localPath = config.local_path || '';
  shareConfigDialog.minFileSizeFormatted = formatBytes(config.min_file_size || 0);
  shareConfigDialog.moviepilotTransfer = config.moviepilot_transfer || false;
  shareConfigDialog.autoDownloadMediainfo = config.auto_download_mediainfo || false;
  shareConfigDialog.mediaServerRefresh = config.media_server_refresh || false;
  shareConfigDialog.scrapeMetadata = config.scrape_metadata || false;
  shareConfigDialog.error = null;
  shareConfigDialog.show = true;
};

const removeShareConfig = (index) => {
  if (index >= 0 && index < shareDialog.configs.length) {
    shareDialog.configs.splice(index, 1);
  }
};

const clearShareConfigs = () => {
  if (shareDialog.configs.length === 0) return;
  if (confirm('确定要清空所有分享配置吗？此操作不可恢复。')) {
    shareDialog.configs = [];
  }
};

const saveShareConfigs = async () => {
  shareConfigSaving.value = true;
  shareDialog.error = null;
  try {
    const pluginId = "P115StrmHelper";
    if (props.initialConfig) {
      // 更新配置
      props.initialConfig.share_strm_config = shareDialog.configs;
      props.initialConfig.share_strm_mediaservers = shareDialog.globalMediaservers.length > 0
        ? [...shareDialog.globalMediaservers]
        : null;
      props.initialConfig.share_strm_mp_mediaserver_paths = shareDialog.globalMpMediaserverPaths || null;

      const result = await props.api.post(`plugin/${pluginId}/save_config`, props.initialConfig);
      if (result && result.code === 0) {
        actionMessage.value = '分享配置已保存';
        actionMessageType.value = 'success';
        await getStatus();
        closeShareDialog();
      } else {
        throw new Error(result?.msg || '保存配置失败');
      }
    } else {
      throw new Error('配置对象不存在');
    }
  } catch (err) {
    shareDialog.error = `保存配置失败: ${err.message || '未知错误'}`;
    console.error('保存配置失败:', err);
  } finally {
    shareConfigSaving.value = false;
  }
};

const closeShareConfigDialog = () => {
  shareConfigDialog.show = false;
  shareConfigDialog.editingIndex = -1;
  shareConfigDialog.error = null;
};

const saveShareConfig = () => {
  shareConfigDialog.error = null;
  if (!isShareConfigDialogValid.value) {
    shareConfigDialog.error = '请填写必填项';
    return;
  }

  const config = {
    share_link: shareConfigDialog.shareLink || null,
    share_code: shareConfigDialog.shareCode || null,
    share_receive: shareConfigDialog.shareReceive || null,
    share_path: shareConfigDialog.sharePath || null,
    local_path: shareConfigDialog.localPath,
    min_file_size: parseSize(shareConfigDialog.minFileSizeFormatted) || null,
    moviepilot_transfer: shareConfigDialog.moviepilotTransfer,
    auto_download_mediainfo: shareConfigDialog.moviepilotTransfer ? false : shareConfigDialog.autoDownloadMediainfo,
    media_server_refresh: shareConfigDialog.moviepilotTransfer ? false : shareConfigDialog.mediaServerRefresh,
    scrape_metadata: shareConfigDialog.moviepilotTransfer ? false : shareConfigDialog.scrapeMetadata,
  };

  if (shareConfigDialog.editingIndex >= 0) {
    shareDialog.configs[shareConfigDialog.editingIndex] = config;
  } else {
    shareDialog.configs.push(config);
  }

  closeShareConfigDialog();
};

const openShareConfigDirSelector = (type) => {
  dirDialog.show = true;
  dirDialog.isLocal = type === 'local';
  dirDialog.loading = false;
  dirDialog.error = null;
  dirDialog.items = [];
  dirDialog.currentPath = dirDialog.isLocal ? (shareConfigDialog.localPath || '/') : '/';
  dirDialog.callback = (path) => {
    if (dirDialog.isLocal) shareConfigDialog.localPath = path;
  };
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
      const pluginId = "P115StrmHelper";
      if (!props.initialConfig?.cookies || props.initialConfig?.cookies.trim() === '') {
        throw new Error('请先设置115 Cookie才能浏览网盘目录');
      }
      const result = await props.api.get(`plugin/${pluginId}/browse_dir?path=${encodeURIComponent(dirDialog.currentPath)}&is_local=${dirDialog.isLocal}`);
      // 修改点: 从 result.data 中获取核心数据
      if (result && result.code === 0 && result.data) {
        dirDialog.items = result.data.items.filter(item => item.is_dir);
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
  if (typeof dirDialog.callback === 'function') {
    dirDialog.callback(processedPath);
  }
  closeDirDialog();
};

const closeDirDialog = () => {
  dirDialog.show = false;
  dirDialog.items = [];
  dirDialog.error = null;
};

const executeShareSync = async () => {
  shareSyncLoading.value = true;
  shareDialog.error = null;
  try {
    if (shareDialog.configs.length === 0) {
      throw new Error('请至少添加一个分享配置');
    }

    const pluginId = "P115StrmHelper";
    if (props.initialConfig) {
      // 更新配置
      props.initialConfig.share_strm_config = shareDialog.configs;
      props.initialConfig.share_strm_mediaservers = shareDialog.globalMediaservers.length > 0
        ? [...shareDialog.globalMediaservers]
        : null;
      props.initialConfig.share_strm_mp_mediaserver_paths = shareDialog.globalMpMediaserverPaths || null;

      await props.api.post(`plugin/${pluginId}/save_config`, props.initialConfig);
    }

    const result = await props.api.post(`plugin/${pluginId}/share_sync`);
    if (result && result.code === 0) {
      actionMessage.value = result.msg || '分享同步任务已启动';
      actionMessageType.value = 'success';
      await getStatus();
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

const openOfflineDownloadDialog = () => {
  offlineDownloadDialog.show = true;
  offlineDownloadDialog.activeTab = 'tasks';
  offlineDownloadDialog.error = null;
  offlineDownloadDialog.addError = null;
};

const closeOfflineDownloadDialog = () => {
  offlineDownloadDialog.show = false;
  offlineDownloadDialog.tasks = [];
  offlineDownloadDialog.totalTasks = 0;
  offlineDownloadDialog.links = '';
  offlineDownloadDialog.destPath = '';
};

const fetchOfflineTasks = async ({ page, itemsPerPage }) => {
  offlineDownloadDialog.loading = true;
  offlineDownloadDialog.error = null;
  try {
    const pluginId = "P115StrmHelper";
    const result = await props.api.post(`plugin/${pluginId}/offline_tasks`, {
      page: page,
      limit: itemsPerPage
    });
    if (result && result.code === 0 && result.data) {
      offlineDownloadDialog.tasks = result.data.tasks || [];
      offlineDownloadDialog.totalTasks = result.data.total || 0;
    } else {
      throw new Error(result?.msg || '获取离线任务列表失败');
    }
  } catch (err) {
    offlineDownloadDialog.error = `获取任务失败: ${err.message || '未知错误'}`;
    console.error('获取离线任务失败:', err);
    offlineDownloadDialog.tasks = [];
    offlineDownloadDialog.totalTasks = 0;
  } finally {
    offlineDownloadDialog.loading = false;
  }
};

const addOfflineTask = async () => {
  if (!offlineDownloadDialog.links.trim()) {
    offlineDownloadDialog.addError = '下载链接不能为空。';
    return;
  }
  offlineDownloadDialog.adding = true;
  offlineDownloadDialog.addError = null;
  try {
    const pluginId = "P115StrmHelper";
    const links = offlineDownloadDialog.links.split('\n').map(l => l.trim()).filter(Boolean);
    const result = await props.api.post(`plugin/${pluginId}/add_offline_task`, {
      links: links,
      path: offlineDownloadDialog.destPath || null,
    });
    if (result && result.code === 0) {
      actionMessage.value = result.msg || '离线任务添加成功';
      actionMessageType.value = 'success';
      offlineDownloadDialog.links = '';
      offlineDownloadDialog.destPath = '';
      offlineDownloadDialog.activeTab = 'tasks';
      fetchOfflineTasks({ page: 1, itemsPerPage: offlineDownloadDialog.itemsPerPage });
    } else {
      throw new Error(result?.msg || '添加离线任务失败');
    }
  } catch (err) {
    offlineDownloadDialog.addError = `添加任务失败: ${err.message || '未知错误'}`;
    console.error('添加离线任务失败:', err);
  } finally {
    offlineDownloadDialog.adding = false;
  }
};

const openOfflineDestDirSelector = () => {
  dirDialog.show = true;
  dirDialog.isLocal = false;
  dirDialog.loading = false;
  dirDialog.error = null;
  dirDialog.items = [];
  dirDialog.currentPath = offlineDownloadDialog.destPath || '/';
  dirDialog.callback = (path) => {
    offlineDownloadDialog.destPath = path;
  };
  loadDirContent();
};

const getTaskStatusColor = (status) => {
  switch (status) {
    case 0: return 'info';
    case 1: return 'error';
    case 2: return 'success';
    case 3: return 'warning';
    default: return 'grey';
  }
};

const getParsedPaths = (pathString) => {
  if (!pathString) return [];
  try {
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

watch(() => props.initialConfig, (newConfig) => {
  if (newConfig) {
    status.enabled = newConfig.enabled || false;
    status.has_client = Boolean(newConfig.cookies && newConfig.cookies.trim() !== '');
    if (newConfig.sync_del_enabled) {
      loadSyncDelHistory();
      // 清除旧的定时器
      if (syncDelHistoryRefreshTimer) {
        clearInterval(syncDelHistoryRefreshTimer);
      }
      // 设置定期刷新历史记录（每30秒刷新一次）
      syncDelHistoryRefreshTimer = setInterval(() => {
        if (props.initialConfig?.sync_del_enabled) {
          loadSyncDelHistory();
        }
      }, 30000);
    } else {
      syncDelHistory.value = [];
      // 清除定时器
      if (syncDelHistoryRefreshTimer) {
        clearInterval(syncDelHistoryRefreshTimer);
        syncDelHistoryRefreshTimer = null;
      }
    }
  }
}, { immediate: true });

const loadSyncDelHistory = async () => {
  if (!props.initialConfig?.sync_del_enabled) {
    syncDelHistory.value = [];
    return;
  }
  syncDelHistoryLoading.value = true;
  try {
    const pluginId = "P115StrmHelper";
    const response = await props.api.get(`plugin/${pluginId}/get_sync_del_history`);
    if (response && response.success && response.data) {
      syncDelHistory.value = Array.isArray(response.data)
        ? response.data.sort((a, b) => {
          const timeA = new Date(a.del_time || 0).getTime();
          const timeB = new Date(b.del_time || 0).getTime();
          return timeB - timeA;
        })
        : [];
    } else {
      syncDelHistory.value = [];
    }
  } catch (err) {
    console.error('加载同步删除历史失败:', err);
    syncDelHistory.value = [];
  } finally {
    syncDelHistoryLoading.value = false;
  }
};

const deleteSyncDelHistory = async (unique) => {
  if (!unique) return;
  deletingHistoryId.value = unique;
  try {
    const pluginId = "P115StrmHelper";
    const apiToken = await props.api.get('system/config?key=app.api_token');
    const token = apiToken?.value || '';
    const response = await props.api.get(
      `plugin/${pluginId}/delete_sync_del_history?key=${encodeURIComponent(unique)}&apikey=${encodeURIComponent(token)}`
    );
    if (response && response.success) {
      actionMessage.value = '删除成功';
      actionMessageType.value = 'success';
      await loadSyncDelHistory();
    } else {
      actionMessage.value = response?.message || '删除失败';
      actionMessageType.value = 'error';
    }
  } catch (err) {
    console.error('删除同步删除历史失败:', err);
    actionMessage.value = `删除失败: ${err.message || '未知错误'}`;
    actionMessageType.value = 'error';
  } finally {
    deletingHistoryId.value = null;
  }
};

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
  try {
    const pluginId = "P115StrmHelper";
    const data = await props.api.get(`plugin/${pluginId}/get_config`);
    if (data && data.mediaservers) {
      mediaservers.value = data.mediaservers;
    }
  } catch (err) {
    console.error('加载媒体服务器列表失败:', err);
  }
  if (props.initialConfig?.sync_del_enabled) {
    await loadSyncDelHistory();
    // 设置定期刷新历史记录（每30秒刷新一次）
    syncDelHistoryRefreshTimer = setInterval(() => {
      if (props.initialConfig?.sync_del_enabled) {
        loadSyncDelHistory();
      }
    }, 30000);
  }
});

// 清理定时器
onBeforeUnmount(() => {
  if (syncDelHistoryRefreshTimer) {
    clearInterval(syncDelHistoryRefreshTimer);
    syncDelHistoryRefreshTimer = null;
  }
});

async function fetchUserStorageStatus() {
  userInfo.loading = true;
  userInfo.error = null;
  storageInfo.loading = true;
  storageInfo.error = null;
  try {
    const pluginId = "P115StrmHelper";
    // 此接口未包裹在 ApiResponse 中，保持不变
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
</script>

<style scoped>
.plugin-page {
  padding: 12px;
}

.plugin-page :deep(.v-card) {
  border-radius: 16px !important;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

.config-card {
  border-radius: 12px !important;
  box-shadow: none !important;
  border: 1px solid rgba(var(--v-border-color), 0.12) !important;
  background: rgba(var(--v-theme-surface), 1) !important;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
  margin-bottom: 16px !important;
  overflow: hidden;
}

.config-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.08) !important;
  border-color: rgba(var(--v-theme-primary), 0.2) !important;
}

/* 统一字体 */
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

/* 文字大小 */
:deep(.text-caption) {
  font-size: 0.8rem !important;
}

:deep(.text-body-2) {
  font-size: 0.85rem !important;
}

/* 美化卡片标题 */
.bg-primary-gradient {
  background: linear-gradient(135deg, rgba(var(--v-theme-primary), 0.12), rgba(var(--v-theme-primary), 0.06)) !important;
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(var(--v-border-color), 0.08) !important;
}

.plugin-page :deep(.v-card-title) {
  border-radius: 12px 12px 0 0;
}

/* 美化芯片 */
:deep(.v-chip) {
  font-weight: 500;
  border-radius: 8px !important;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05) !important;
}

:deep(.v-chip--selected),
:deep(.v-chip:hover) {
  transform: translateY(-1px);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.1) !important;
}

:deep(.v-list-item) {
  min-height: 36px;
  /* More compact list items */
}

:deep(.v-list-item--density-default:not(.v-list-item--nav).v-list-item--one-line) {
  padding-inline-start: 12px !important;
  /* Reduced from 16px */
  padding-inline-end: 12px !important;
  /* Reduced from 16px */
}

/* Ensure py-0 on list items takes effect with Vuetify's defaults if used specifically */
.py-0 {
  padding-top: 0 !important;
  padding-bottom: 0 !important;
}

.my-0 {
  margin-top: 0 !important;
  margin-bottom: 0 !important;
}

/* Reduce margin for subtitles in path display if they have mt-1 */
.mt-1 {
  margin-top: 2px !important;
  /* Reduced from default 4px of Vuetify's mt-1 */
}

/* Reduce margin for subtitles in path display if they have mb-1 */
.mb-1 {
  margin-bottom: 2px !important;
  /* Reduced from default 4px of Vuetify's mb-1 */
}

.sticky-actions {
  position: sticky;
  bottom: 0;
  background: linear-gradient(to top, rgba(var(--v-theme-surface), 0.98), rgba(var(--v-theme-surface), 0.95));
  backdrop-filter: blur(10px);
  z-index: 2;
  border-top: 1px solid rgba(var(--v-border-color), 0.1);
  border-radius: 0 0 16px 16px;
}

/* 优化按钮样式 */
:deep(.v-btn) {
  border-radius: 8px !important;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;
  font-weight: 500 !important;
}

:deep(.v-btn:hover) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15) !important;
}

/* 优化警告框样式 */
:deep(.v-alert) {
  border-radius: 10px !important;
  border-left-width: 4px !important;
  transition: all 0.25s ease !important;
}

/* 优化列表项样式 */
:deep(.v-list-item) {
  border-radius: 8px;
  margin: 2px 4px;
  transition: all 0.2s ease !important;
}

:deep(.v-list-item:hover) {
  background-color: rgba(var(--v-theme-primary), 0.04) !important;
}

/* Colorful Switches */
:deep(.v-switch .v-selection-control--dirty .v-track) {
  opacity: 0.8 !important;
  /* Ensure high opacity for color visibility */
}

:deep(.v-switch .v-selection-control--dirty .v-selection-control__input > .v-icon) {
  color: white !important;
}

/* Primary Color Switch */
:deep(v-switch[color="primary"] .v-selection-control--dirty .v-track),
:deep(v-switch[color="primary"] .v-selection-control--dirty .v-switch__track) {
  /* track */
  background-color: rgb(var(--v-theme-primary)) !important;
  border-color: rgb(var(--v-theme-primary)) !important;
}

/* Success Color Switch */
:deep(v-switch[color="success"] .v-selection-control--dirty .v-track),
:deep(v-switch[color="success"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-success)) !important;
  border-color: rgb(var(--v-theme-success)) !important;
}

/* Info Color Switch */
:deep(v-switch[color="info"] .v-selection-control--dirty .v-track),
:deep(v-switch[color="info"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-info)) !important;
  border-color: rgb(var(--v-theme-info)) !important;
}

/* Warning Color Switch */
:deep(v-switch[color="warning"] .v-selection-control--dirty .v-track),
:deep(v-switch[color="warning"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-warning)) !important;
  border-color: rgb(var(--v-theme-warning)) !important;
}

/* Error Color Switch */
:deep(v-switch[color="error"] .v-selection-control--dirty .v-track),
:deep(v-switch[color="error"] .v-selection-control--dirty .v-switch__track) {
  background-color: rgb(var(--v-theme-error)) !important;
  border-color: rgb(var(--v-theme-error)) !important;
}

.icon-spin-animation {
  animation: spin 2s linear infinite;
}

@keyframes spin {
  0% {
    transform: rotate(0deg);
  }

  100% {
    transform: rotate(360deg);
  }
}

/* 手机端优化 */
@media (max-width: 768px) {
  .plugin-page {
    padding: 8px;
  }

  /* 移动端减小圆角 */
  .plugin-page :deep(.v-card) {
    border-radius: 12px !important;
  }

  .config-card {
    border-radius: 10px !important;
    margin-bottom: 12px !important;
  }

  .plugin-page :deep(.v-card-title) {
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

  /* 优化列表项触摸区域 */
  :deep(.v-list-item) {
    min-height: 48px !important;
    padding: 8px 12px !important;
  }

  /* 优化输入框 */
  :deep(.v-field) {
    border-radius: 8px !important;
  }

  :deep(.v-text-field .v-field),
  :deep(.v-select .v-field),
  :deep(.v-textarea .v-field) {
    min-height: 48px !important;
  }

  /* 优化对话框在移动端 */
  :deep(.v-dialog > .v-card) {
    margin: 16px !important;
    max-height: calc(100vh - 32px) !important;
    border-radius: 16px !important;
  }

  /* 离线下载表格优化 */
  :deep(.v-data-table) {
    font-size: 0.8rem;
  }

  :deep(.v-data-table .v-data-table__td) {
    padding: 10px 6px !important;
    min-height: 44px !important;
  }

  :deep(.v-data-table .v-data-table__th) {
    padding: 10px 6px !important;
    font-size: 0.75rem;
    min-height: 44px !important;
  }

  /* 防止表格在手机上的触摸滑动触发标签页切换 */
  :deep(.v-data-table) {
    touch-action: pan-x;
  }

  /* 优化进度条在手机上的显示 */
  :deep(.v-progress-linear) {
    height: 8px !important;
  }

  /* 优化芯片在手机上的显示 */
  :deep(.v-chip) {
    font-size: 0.75rem !important;
    height: 28px !important;
    padding: 0 10px !important;
    min-height: 28px !important;
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

  /* 优化图标大小 */
  :deep(.v-icon) {
    font-size: 20px !important;
  }

  :deep(.v-icon--size-small) {
    font-size: 18px !important;
  }

  /* 同步删除历史对话框在移动端的滚动优化 */
  :deep(.sync-del-history-content) {
    max-height: calc(100vh - 120px) !important;
    overflow-y: auto !important;
    -webkit-overflow-scrolling: touch !important;
  }

  :deep(.sync-del-history-content .v-list) {
    max-height: none !important;
    overflow-y: visible !important;
  }
}
</style>