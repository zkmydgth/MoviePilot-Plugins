<template>
  <div class="dashboard-widget" :class="{
    'dashboard-widget--strm': panelKey === 'strm',
    'dashboard-widget--status': panelKey === 'status',
    'dashboard-widget--sync-del': panelKey === 'sync_del',
    'dashboard-widget--transfer-mini': panelKey === 'manual_transfer',
  }">
    <!-- STRM 同步执行记录 -->
    <v-card v-if="panelKey === 'strm'" :flat="!props.config?.attrs?.border"
      class="strm-dash-card fill-height d-flex flex-column">
      <v-card-item v-if="props.config?.attrs?.title || props.config?.attrs?.subtitle" class="strm-dash-card__head pb-2">
        <v-card-title class="d-flex flex-wrap align-center gap-2 ps-0">
          <v-icon icon="mdi-sync" color="primary" size="small" />
          <span>{{ props.config?.attrs?.title || "STRM 同步执行记录" }}</span>
          <v-chip v-if="initialDataLoaded" size="small" color="primary" variant="tonal">
            {{ strmHistoryTotal }} 条
          </v-chip>
        </v-card-title>
        <v-card-subtitle v-if="props.config?.attrs?.subtitle">{{ props.config.attrs.subtitle }}</v-card-subtitle>
        <template v-slot:append>
          <div class="d-flex align-center flex-shrink-0">
            <v-btn v-if="strmHistoryTotal > 0" size="small" variant="text" color="error" class="mr-1"
              prepend-icon="mdi-delete-sweep" :loading="deletingAllStrm" @click="confirmDeleteAllStrm">
              清空
            </v-btn>
            <v-btn icon size="small" variant="text" :loading="strmHistoryLoading" @click="loadStrmHistory">
              <v-icon>mdi-refresh</v-icon>
            </v-btn>
          </div>
        </template>
      </v-card-item>

      <v-card-text class="flex-grow-1 pa-3 pt-0 d-flex flex-column strm-dash-card__body">
        <v-alert v-if="strmBannerMessage" :type="strmBannerType" variant="tonal" density="comfortable" class="mb-3"
          closable @click:close="strmBannerMessage = ''">
          {{ strmBannerMessage }}
        </v-alert>

        <div v-if="strmHistoryLoading && !initialDataLoaded" class="text-center py-8">
          <v-progress-circular indeterminate color="primary" size="40" />
        </div>

        <div v-else-if="strmLoadError" class="text-error text-body-2 d-flex align-center">
          <v-icon size="small" color="error" class="mr-1">mdi-alert-circle-outline</v-icon>
          {{ strmLoadError }}
        </div>

        <template v-else-if="initialDataLoaded">
          <div class="strm-filter-row d-flex flex-wrap align-center gap-2 mb-3">
            <v-select v-model="strmKindSelected" :items="strmKindSelectItems" item-title="title" item-value="value"
              label="任务类型" density="compact" hide-details clearable variant="outlined" class="strm-kind-field"
              @update:model-value="applyStrmFilter" />
          </div>

          <v-skeleton-loader v-if="strmHistoryLoading" type="card, card, card" class="bg-transparent" />

          <div v-else-if="strmHistoryItems.length === 0" class="text-center py-10 strm-dash-empty">
            <v-icon icon="mdi-playlist-remove" size="56" color="grey" class="mb-3 opacity-40" />
            <div class="text-body-1 text-medium-emphasis">暂无执行记录</div>
            <div class="text-caption text-medium-emphasis mt-1">完成一次全量、增量或分享同步后将在此展示</div>
          </div>

          <div v-else class="strm-dash-list flex-grow-1">
            <v-card v-for="(row, idx) in strmHistoryItems" :key="row.unique || idx" variant="outlined"
              class="strm-dash-item strm-history-item mb-3"
              :class="{ 'strm-dash-item--fail strm-history-item--fail': !row.success }">
              <v-card-item class="pb-2">
                <div class="flex-grow-1 min-w-0">
                  <div class="d-flex flex-wrap align-center gap-2 mb-2">
                    <v-chip size="small" color="primary" variant="flat">{{ kindLabel(row.kind) }}</v-chip>
                    <v-chip size="small" :color="row.success ? 'success' : 'error'" variant="tonal">
                      {{ row.success ? "成功" : "失败" }}
                    </v-chip>
                  </div>
                  <div class="text-h6 font-weight-medium text-high-emphasis">
                    {{ row.finished_at }}
                  </div>
                  <div class="text-body-2 text-medium-emphasis mt-2">
                    耗时 <strong class="text-high-emphasis">{{ formatNum(row.elapsed_sec) }}</strong> 秒 · 扫描
                    <strong class="text-high-emphasis">{{ row.total_iterated }}</strong> 项
                    <template v-if="row.kind === 'increment'">
                      · API <strong class="text-high-emphasis">{{ row.api_requests }}</strong> 次
                    </template>
                  </div>
                </div>
                <template v-slot:append>
                  <v-btn icon size="small" variant="text" color="error" :loading="deletingStrmId === row.unique"
                    :disabled="!row.unique" @click="confirmDeleteStrm(row)">
                    <v-icon size="small">mdi-delete-outline</v-icon>
                  </v-btn>
                </template>
              </v-card-item>
              <v-card-text class="pt-0">
                <div class="text-caption text-medium-emphasis strm-section-label mb-2">生成统计</div>
                <div class="d-flex flex-wrap gap-1 mb-1">
                  <v-chip v-for="s in parseStatsEntries(row.stats, row.kind)" :key="s.key" size="small"
                    :color="statChipColor(s)" variant="tonal">
                    {{ s.label }} {{ formatStatValue(s.value) }}
                  </v-chip>
                </div>
                <div v-if="parseExtraEntries(row.extra).length" class="mt-3">
                  <div class="text-caption text-medium-emphasis strm-section-label mb-2">补充信息</div>
                  <div v-for="(ex, ei) in parseExtraEntries(row.extra)" :key="ei" class="text-body-2 strm-extra-line">
                    <span class="text-medium-emphasis">{{ ex.label }}</span>
                    <a v-if="ex.href" :href="ex.href" target="_blank" rel="noopener noreferrer"
                      class="text-primary ms-1">{{
                        ex.display }}</a>
                    <span v-else class="ms-1 text-high-emphasis" :title="ex.full">{{ ex.display }}</span>
                  </div>
                </div>
                <v-alert v-if="!row.success && row.error" type="error" variant="tonal" density="compact" class="mt-3"
                  border="start">
                  {{ row.error }}
                </v-alert>
              </v-card-text>
            </v-card>
          </div>

          <div v-if="strmHistoryTotal > 0 && !strmHistoryLoading"
            class="d-flex flex-wrap align-center justify-space-between mt-2 gap-2">
            <div class="text-caption text-medium-emphasis">
              {{ strmPageRangeText }}
            </div>
            <v-pagination v-model="strmHistoryPage" :length="strmPaginationLength" :total-visible="7"
              density="comfortable" @update:model-value="loadStrmHistory" />
          </div>
        </template>
      </v-card-text>

      <v-divider v-if="props.allowRefresh" />
      <v-card-actions v-if="props.allowRefresh" class="px-3 py-2 refresh-actions">
        <span class="text-caption text-disabled">{{ lastRefreshedTimeDisplay }}</span>
        <v-spacer />
        <v-btn icon variant="text" size="small" :loading="strmHistoryLoading" @click="loadStrmHistory">
          <v-icon size="small">mdi-refresh</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-dialog v-if="panelKey === 'strm'" v-model="deleteStrmConfirm.show" max-width="420" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="error" class="mr-2" />
          删除记录
        </v-card-title>
        <v-card-text>确定删除这条 STRM 执行历史吗？</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="grey" variant="text" :disabled="deletingStrmId" @click="deleteStrmConfirm.show = false">
            取消
          </v-btn>
          <v-btn color="error" variant="text" :loading="deletingStrmId" @click="handleConfirmDeleteStrm">
            删除
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-if="panelKey === 'strm'" v-model="deleteAllStrmConfirm" max-width="450" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="error" class="mr-2" />
          清空历史
        </v-card-title>
        <v-card-text>
          <div class="mb-2">确定清空全部 <strong>{{ strmHistoryTotal }}</strong> 条 STRM 执行历史吗？</div>
          <v-alert type="error" variant="tonal" density="compact" class="mt-2" icon="mdi-alert">
            <div class="text-caption">此操作不可恢复</div>
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="grey" variant="text" :disabled="deletingAllStrm" @click="deleteAllStrmConfirm = false">
            取消
          </v-btn>
          <v-btn color="error" variant="text" :loading="deletingAllStrm" @click="handleConfirmDeleteAllStrm">
            确认清空
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <!-- 运行状态 + 115 账户 -->
    <v-card v-else-if="panelKey === 'status'" :flat="!props.config?.attrs?.border" :loading="loading"
      class="status-dash-card fill-height d-flex flex-column">
      <v-card-item v-if="props.config?.attrs?.title || props.config?.attrs?.subtitle" class="pb-2">
        <v-card-title>{{ props.config?.attrs?.title || "115网盘STRM助手" }}</v-card-title>
        <v-card-subtitle v-if="props.config?.attrs?.subtitle">{{ props.config.attrs.subtitle }}</v-card-subtitle>
      </v-card-item>

      <v-card-text class="flex-grow-1 pa-3 pt-0">
        <div v-if="loading && !initialDataLoaded" class="text-center py-8">
          <v-progress-circular indeterminate color="primary" size="40" />
        </div>

        <div v-else-if="error" class="text-error text-body-2 d-flex align-center">
          <v-icon size="small" color="error" class="mr-1">mdi-alert-circle-outline</v-icon>
          {{ error }}
        </div>

        <v-row v-else-if="initialDataLoaded" class="status-dash-row" align="stretch">
          <v-col cols="12" md="6" class="d-flex flex-column mb-3 mb-md-0">
            <v-card class="status-inner-card w-100 d-flex flex-column flex-grow-1" variant="outlined">
              <v-card-item class="py-2">
                <v-card-title class="text-subtitle-1 d-flex align-center">
                  <v-icon icon="mdi-information" class="mr-2" size="small" />
                  系统状态
                </v-card-title>
              </v-card-item>
              <v-divider />
              <v-card-text class="pa-0 flex-grow-1">
                <v-list class="bg-transparent pa-0">
                  <v-list-item class="px-4 py-1" style="min-height: 40px">
                    <template v-slot:prepend>
                      <v-icon :color="status.enabled ? 'success' : 'grey'" icon="mdi-power" size="small" />
                    </template>
                    <v-list-item-title class="text-body-2">插件状态</v-list-item-title>
                    <template v-slot:append>
                      <v-chip :color="status.enabled ? 'success' : 'grey'" size="small" variant="tonal">
                        {{ status.enabled ? "已启用" : "已禁用" }}
                      </v-chip>
                    </template>
                  </v-list-item>
                  <v-divider class="my-0" />
                  <v-list-item class="px-4 py-1" style="min-height: 40px">
                    <template v-slot:prepend>
                      <v-icon :color="status.has_client && initialConfig?.cookies ? 'success' : 'error'"
                        icon="mdi-account-check" size="small" />
                    </template>
                    <v-list-item-title class="text-body-2">115客户端状态</v-list-item-title>
                    <template v-slot:append>
                      <v-chip :color="status.has_client && initialConfig?.cookies ? 'success' : 'error'" size="small"
                        variant="tonal">
                        {{ status.has_client && initialConfig?.cookies ? "已连接" : "未连接" }}
                      </v-chip>
                    </template>
                  </v-list-item>
                  <v-divider class="my-0" />
                  <v-list-item class="px-4 py-1" style="min-height: 40px">
                    <template v-slot:prepend>
                      <v-icon :color="status.running ? 'warning' : 'success'" icon="mdi-play-circle" size="small" />
                    </template>
                    <v-list-item-title class="text-body-2">任务状态</v-list-item-title>
                    <template v-slot:append>
                      <v-chip :color="status.running ? 'warning' : 'success'" size="small" variant="tonal">
                        {{ status.running ? "运行中" : "空闲" }}
                      </v-chip>
                    </template>
                  </v-list-item>
                </v-list>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="6" class="d-flex flex-column">
            <v-card class="status-inner-card w-100 d-flex flex-column flex-grow-1" variant="outlined">
              <v-card-item class="py-2">
                <v-card-title class="text-subtitle-1 d-flex align-center">
                  <v-icon icon="mdi-account-box" class="mr-2" size="small" />
                  115账户信息
                </v-card-title>
              </v-card-item>
              <v-divider />
              <v-card-text class="pa-0 flex-grow-1">
                <v-skeleton-loader v-if="userInfo.loading || storageInfo.loading"
                  type="list-item-avatar-three-line, list-item-three-line" />
                <div v-else>
                  <v-alert v-if="userInfo.error || storageInfo.error" type="warning" variant="tonal"
                    density="comfortable" class="ma-4">
                    {{ userInfo.error || storageInfo.error }}
                  </v-alert>
                  <v-list v-else class="bg-transparent pa-0">
                    <v-list-item class="px-4 py-2">
                      <template v-slot:prepend>
                        <v-avatar size="36" class="mr-2">
                          <v-img v-if="userInfo.avatar" :src="userInfo.avatar" :alt="userInfo.name" />
                          <v-icon v-else icon="mdi-account-circle" />
                        </v-avatar>
                      </template>
                      <v-list-item-title class="text-body-1 font-weight-medium">
                        {{ userInfo.name || "未知用户" }}
                      </v-list-item-title>
                    </v-list-item>
                    <v-divider class="my-0" />
                    <v-list-item class="px-4 py-2">
                      <template v-slot:prepend>
                        <v-icon :color="userInfo.is_vip ? 'amber-darken-2' : 'grey'" icon="mdi-shield-crown"
                          size="small" />
                      </template>
                      <v-list-item-title class="text-body-2">VIP状态</v-list-item-title>
                      <template v-slot:append>
                        <v-chip :color="userInfo.is_vip ? 'success' : 'grey'" size="small" variant="tonal">
                          {{
                            userInfo.is_vip
                              ? userInfo.is_forever_vip
                                ? "永久VIP"
                                : `VIP (至 ${userInfo.vip_expire_date || "N/A"})`
                              : "非VIP"
                          }}
                        </v-chip>
                      </template>
                    </v-list-item>
                    <v-divider class="my-0" />
                    <v-list-item class="px-4 py-3">
                      <v-list-item-title class="text-body-2 mb-1">存储空间</v-list-item-title>
                      <v-list-item-subtitle v-if="storageInfo.used && storageInfo.total" class="text-caption">
                        已用 {{ storageInfo.used }} / 总共 {{ storageInfo.total }} (剩余 {{ storageInfo.remaining }})
                      </v-list-item-subtitle>
                      <v-progress-linear v-if="storageInfo.used && storageInfo.total"
                        :model-value="calculateStoragePercentage(storageInfo.used, storageInfo.total)" color="primary"
                        height="8" rounded class="mt-2" />
                      <v-list-item-subtitle v-else class="text-caption text-medium-emphasis">
                        存储信息不可用
                      </v-list-item-subtitle>
                    </v-list-item>
                  </v-list>
                </div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>
      </v-card-text>

      <v-divider v-if="props.allowRefresh" />
      <v-card-actions v-if="props.allowRefresh" class="px-3 py-2 refresh-actions">
        <span class="text-caption text-disabled">{{ lastRefreshedTimeDisplay }}</span>
        <v-spacer />
        <v-btn icon variant="text" size="small" @click="fetchStatusData" :loading="loading">
          <v-icon size="small">mdi-refresh</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>

    <!-- 同步删除历史（与 Page 弹窗同能力：分页、单删、清空） -->
    <v-card v-else-if="panelKey === 'sync_del'" :flat="!props.config?.attrs?.border"
      class="strm-dash-card sync-del-dash-card fill-height d-flex flex-column">
      <v-card-item v-if="props.config?.attrs?.title || props.config?.attrs?.subtitle" class="strm-dash-card__head pb-2">
        <v-card-title class="d-flex flex-wrap align-center gap-2 ps-0">
          <v-icon icon="mdi-delete-sweep" color="warning" size="small" />
          <span>{{ props.config?.attrs?.title || "同步删除历史" }}</span>
          <v-chip v-if="syncDelInitialLoaded" size="small" color="warning" variant="tonal">
            {{ syncDelHistoryTotal }} 条
          </v-chip>
        </v-card-title>
        <v-card-subtitle v-if="props.config?.attrs?.subtitle">{{ props.config.attrs.subtitle }}</v-card-subtitle>
        <template v-slot:append>
          <div class="d-flex align-center flex-shrink-0">
            <v-btn v-if="syncDelHistoryTotal > 0" size="small" variant="text" color="error" class="mr-1"
              prepend-icon="mdi-delete-sweep" :loading="deletingAllSyncDel" @click="confirmDeleteAllSyncDel">
              清空
            </v-btn>
            <v-btn icon size="small" variant="text" :loading="syncDelHistoryLoading" @click="loadSyncDelHistory">
              <v-icon>mdi-refresh</v-icon>
            </v-btn>
          </div>
        </template>
      </v-card-item>

      <v-card-text class="flex-grow-1 pa-3 pt-0 d-flex flex-column sync-del-dash-card__body">
        <v-alert v-if="syncDelBannerMessage" :type="syncDelBannerType" variant="tonal" density="comfortable"
          class="mb-3" closable @click:close="syncDelBannerMessage = ''">
          {{ syncDelBannerMessage }}
        </v-alert>

        <div v-if="syncDelHistoryLoading && !syncDelInitialLoaded" class="text-center py-8">
          <v-progress-circular indeterminate color="primary" size="40" />
        </div>

        <div v-else-if="syncDelLoadError" class="text-error text-body-2 d-flex align-center">
          <v-icon size="small" color="error" class="mr-1">mdi-alert-circle-outline</v-icon>
          {{ syncDelLoadError }}
        </div>

        <template v-else-if="syncDelInitialLoaded">
          <v-skeleton-loader v-if="syncDelHistoryLoading" type="list-item-avatar-three-line@3" class="bg-transparent" />

          <div v-else-if="syncDelHistory.length === 0" class="text-center py-10">
            <v-icon icon="mdi-information-outline" size="56" color="grey" class="mb-3 opacity-40" />
            <div class="text-body-1 text-medium-emphasis">暂无删除历史</div>
            <div class="text-caption text-medium-emphasis mt-1">媒体同步删除任务完成后将在此记录</div>
          </div>

          <div v-else class="sync-del-list flex-grow-1">
            <v-list class="bg-transparent pa-0">
              <template v-for="(item, index) in syncDelHistory" :key="item.unique || index">
                <v-list-item class="px-0 py-2">
                  <template v-slot:prepend>
                    <v-avatar size="48" rounded class="mr-3">
                      <v-img v-if="item.image" :src="item.image" :alt="item.title" cover />
                      <v-icon v-else icon="mdi-movie" />
                    </v-avatar>
                  </template>
                  <v-list-item-title class="text-body-1 font-weight-medium">{{ item.title }}</v-list-item-title>
                  <v-list-item-subtitle class="text-caption">
                    <div class="d-flex flex-wrap align-center gap-1 mt-1">
                      <v-chip size="small" variant="tonal" color="primary">{{ item.type }}</v-chip>
                      <span v-if="item.year" class="text-medium-emphasis">· {{ item.year }}</span>
                      <span v-if="item.season" class="text-medium-emphasis">
                        · S{{ String(item.season).padStart(2, "0") }}
                      </span>
                      <span v-if="item.episode" class="text-medium-emphasis">
                        · E{{ String(item.episode).padStart(2, "0") }}
                      </span>
                    </div>
                    <div v-if="item.path" class="text-medium-emphasis mt-1 sync-del-path">{{ item.path }}</div>
                    <div class="text-medium-emphasis mt-1">{{ item.del_time }}</div>
                  </v-list-item-subtitle>
                  <template v-slot:append>
                    <v-btn icon size="small" variant="text" color="error" :loading="deletingSyncDelId === item.unique"
                      :disabled="!item.unique" @click="confirmDeleteSyncDel(item)">
                      <v-icon>mdi-delete</v-icon>
                    </v-btn>
                  </template>
                </v-list-item>
                <v-divider v-if="index < syncDelHistory.length - 1" class="my-0" />
              </template>
            </v-list>
          </div>

          <div v-if="syncDelHistoryTotal > 0 && !syncDelHistoryLoading"
            class="d-flex flex-wrap align-center justify-space-between mt-2 gap-2">
            <div class="text-caption text-medium-emphasis">
              {{ syncDelPageRangeText }}
            </div>
            <v-pagination v-model="syncDelHistoryPage" :length="syncDelPaginationLength" :total-visible="7"
              density="comfortable" @update:model-value="loadSyncDelHistory" />
          </div>
        </template>
      </v-card-text>

      <v-divider v-if="props.allowRefresh" />
      <v-card-actions v-if="props.allowRefresh" class="px-3 py-2 refresh-actions">
        <span class="text-caption text-disabled">{{ lastRefreshedTimeDisplaySyncDel }}</span>
        <v-spacer />
        <v-btn icon variant="text" size="small" :loading="syncDelHistoryLoading" @click="loadSyncDelHistory">
          <v-icon size="small">mdi-refresh</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>

    <!-- 手动网盘整理（紧凑卡片，与插件配置中的网盘整理路径一致） -->
    <v-card v-else-if="panelKey === 'manual_transfer'" :flat="!props.config?.attrs?.border"
      class="strm-dash-card transfer-mini-card fill-height d-flex flex-column">
      <v-card-item v-if="props.config?.attrs?.title || props.config?.attrs?.subtitle" class="pb-2">
        <v-card-title class="d-flex flex-wrap align-center gap-2 ps-0 text-subtitle-1">
          <v-icon icon="mdi-folder-cog" color="primary" size="small" />
          {{ props.config?.attrs?.title || "手动网盘整理" }}
        </v-card-title>
        <v-card-subtitle v-if="props.config?.attrs?.subtitle">{{ props.config.attrs.subtitle }}</v-card-subtitle>
        <template v-slot:append>
          <v-btn icon size="small" variant="text" :loading="transferConfigLoading" @click="loadTransferConfig">
            <v-icon>mdi-refresh</v-icon>
          </v-btn>
        </template>
      </v-card-item>

      <v-card-text class="pa-3 pt-0 transfer-mini-card__body">
        <div v-if="transferConfigLoading && !transferConfigLoaded" class="text-center py-6">
          <v-progress-circular indeterminate color="primary" size="36" />
        </div>
        <template v-else-if="transferConfigLoaded">
          <v-alert v-if="!panTransferEnabled" type="warning" variant="tonal" density="compact" class="mb-3">
            请先在插件设置中开启「网盘整理」
          </v-alert>
          <v-alert v-else-if="panTransferSelectItems.length === 0" type="info" variant="tonal" density="compact"
            class="mb-3">
            请先在插件设置中配置「网盘整理目录」
          </v-alert>
          <template v-else>
            <v-select v-model="selectedManualPath" :items="panTransferSelectItems" item-title="title" item-value="value"
              label="整理目录" density="compact" hide-details variant="outlined" class="mb-3" />
            <v-btn block color="primary" variant="tonal" prepend-icon="mdi-play-circle" :disabled="!selectedManualPath"
              @click="openManualTransferDialog">
              执行整理
            </v-btn>
          </template>
        </template>
      </v-card-text>

      <v-divider v-if="props.allowRefresh" />
      <v-card-actions v-if="props.allowRefresh" class="px-3 py-2 refresh-actions">
        <span class="text-caption text-disabled">{{ lastRefreshedTimeDisplayTransfer }}</span>
        <v-spacer />
        <v-btn icon variant="text" size="small" :loading="transferConfigLoading" @click="loadTransferConfig">
          <v-icon size="small">mdi-refresh</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>

    <v-dialog v-if="panelKey === 'sync_del'" v-model="deleteSyncDelConfirm.show" max-width="420" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="error" class="mr-2" />
          删除记录
        </v-card-title>
        <v-card-text>确定删除这条同步删除历史吗？</v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="grey" variant="text" :disabled="deletingSyncDelId" @click="deleteSyncDelConfirm.show = false">
            取消
          </v-btn>
          <v-btn color="error" variant="text" :loading="deletingSyncDelId" @click="handleConfirmDeleteSyncDel">
            删除
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-if="panelKey === 'sync_del'" v-model="deleteAllSyncDelConfirm" max-width="450" persistent>
      <v-card>
        <v-card-title class="text-h6 d-flex align-center">
          <v-icon icon="mdi-alert-circle-outline" color="error" class="mr-2" />
          清空历史
        </v-card-title>
        <v-card-text>
          <div class="mb-2">确定清空全部 <strong>{{ syncDelHistoryTotal }}</strong> 条同步删除历史吗？</div>
          <v-alert type="error" variant="tonal" density="compact" class="mt-2" icon="mdi-alert">
            <div class="text-caption">此操作不可恢复</div>
          </v-alert>
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn color="grey" variant="text" :disabled="deletingAllSyncDel" @click="deleteAllSyncDelConfirm = false">
            取消
          </v-btn>
          <v-btn color="error" variant="text" :loading="deletingAllSyncDel" @click="handleConfirmDeleteAllSyncDel">
            确认清空
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <v-dialog v-if="panelKey === 'manual_transfer'" v-model="manualTransferDialog.show" max-width="500" persistent>
      <v-card>
        <v-card-title class="text-subtitle-1 d-flex align-center px-3 py-2">
          <v-icon icon="mdi-play-circle" class="mr-2" color="primary" size="small" />
          手动整理确认
        </v-card-title>
        <v-card-text class="px-3 py-3">
          <div v-if="!manualTransferDialog.loading && !manualTransferDialog.result">
            <div class="text-body-2 mb-3">确定要手动整理以下目录吗？</div>
            <v-alert type="info" variant="tonal" density="compact" icon="mdi-information">
              <div class="text-body-2"><strong>路径：</strong>{{ manualTransferDialog.path }}</div>
            </v-alert>
          </div>
          <div v-else-if="manualTransferDialog.loading" class="d-flex flex-column align-center py-3">
            <v-progress-circular indeterminate color="primary" size="48" class="mb-3" />
            <div class="text-body-2 text-medium-emphasis">正在启动整理任务...</div>
          </div>
          <div v-else-if="manualTransferDialog.result">
            <v-alert :type="manualTransferDialog.result.type" variant="tonal" density="compact"
              :icon="manualTransferDialog.result.type === 'success' ? 'mdi-check-circle' : 'mdi-alert-circle'">
              <div class="text-subtitle-2 mb-1">{{ manualTransferDialog.result.title }}</div>
              <div class="text-body-2">{{ manualTransferDialog.result.message }}</div>
            </v-alert>
          </div>
        </v-card-text>
        <v-card-actions class="px-3 py-2">
          <v-spacer />
          <template v-if="!manualTransferDialog.loading && !manualTransferDialog.result">
            <v-btn color="grey" variant="text" size="small" @click="closeManualTransferDialog">取消</v-btn>
            <v-btn color="primary" variant="text" size="small" @click="confirmManualTransfer">确认执行</v-btn>
          </template>
          <v-btn v-else color="primary" variant="text" size="small" @click="closeManualTransferDialog">关闭</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from "vue";
import { ensureSentryInitialized } from "../utils/init-sentry.js";
import {
  KIND_LABELS,
  kindLabel,
  parseStatsEntries,
  parseExtraEntries,
  statChipColor,
  formatStatValue,
  formatNum,
} from "../utils/strmHistoryDisplay.js";

const props = defineProps({
  api: {
    type: [Object, Function],
    required: true,
  },
  config: {
    type: Object,
    default: () => ({ attrs: {} }),
  },
  allowRefresh: {
    type: Boolean,
    default: false,
  },
  refreshInterval: {
    type: Number,
    default: 0,
  },
});

/** 与后端 get_dashboard_meta 的 key 一致：strm | status | sync_del | manual_transfer */
const panelKey = computed(() => {
  const k = (props.config?.key ?? "").trim();
  if (k === "status") return "status";
  if (k === "sync_del") return "sync_del";
  if (k === "manual_transfer") return "manual_transfer";
  return "strm";
});

const loading = ref(false);
const error = ref(null);
const initialDataLoaded = ref(false);
const lastRefreshedTimestamp = ref(null);

const strmKindSelected = ref("");
const strmKindApplied = ref("");
const strmHistoryPage = ref(1);
const strmHistoryLimit = ref(20);
const strmHistoryItems = ref([]);
const strmHistoryTotal = ref(0);
const strmHistoryLoading = ref(false);
const strmLoadError = ref(null);
const strmBannerMessage = ref("");
const strmBannerType = ref("info");
const deletingStrmId = ref(null);
const deletingAllStrm = ref(false);
const deleteStrmConfirm = reactive({ show: false, row: null });
const deleteAllStrmConfirm = ref(false);

const syncDelHistory = ref([]);
const syncDelHistoryLoading = ref(false);
const syncDelHistoryTotal = ref(0);
const syncDelHistoryPage = ref(1);
const syncDelHistoryLimit = ref(20);
const syncDelInitialLoaded = ref(false);
const syncDelLoadError = ref(null);
const syncDelBannerMessage = ref("");
const syncDelBannerType = ref("info");
const deletingSyncDelId = ref(null);
const deletingAllSyncDel = ref(false);
const deleteSyncDelConfirm = reactive({ show: false, item: null });
const deleteAllSyncDelConfirm = ref(false);
const lastRefreshedTimestampSyncDel = ref(null);

const transferConfigLoading = ref(false);
const transferConfigLoaded = ref(false);
const panTransferEnabled = ref(false);
const panTransferPathsList = ref([]);
const selectedManualPath = ref("");
const lastRefreshedTimestampTransfer = ref(null);
const manualTransferDialog = reactive({
  show: false,
  loading: false,
  path: "",
  result: null,
});

const strmKindSelectItems = [
  { title: "全部类型", value: "" },
  ...Object.entries(KIND_LABELS).map(([value, title]) => ({ title, value })),
];

const status = reactive({
  enabled: false,
  has_client: false,
  running: false,
});

const initialConfig = reactive({});

const userInfo = reactive({
  name: null,
  is_vip: null,
  is_forever_vip: null,
  vip_expire_date: null,
  avatar: null,
  error: null,
  loading: true,
});

const storageInfo = reactive({
  total: null,
  used: null,
  remaining: null,
  error: null,
  loading: true,
});

let refreshTimer = null;

const getPluginId = () => "P115StrmHelper";

const strmPaginationLength = computed(() => {
  if (strmHistoryTotal.value <= 0 || strmHistoryLimit.value <= 0) return 0;
  return Math.ceil(strmHistoryTotal.value / strmHistoryLimit.value);
});

const strmPageRangeText = computed(() => {
  if (strmHistoryTotal.value <= 0) return "";
  const start = (strmHistoryPage.value - 1) * strmHistoryLimit.value + 1;
  const end = Math.min(strmHistoryPage.value * strmHistoryLimit.value, strmHistoryTotal.value);
  return `第 ${start} – ${end} 条，共 ${strmHistoryTotal.value} 条`;
});

const syncDelPaginationLength = computed(() => {
  if (syncDelHistoryTotal.value <= 0 || syncDelHistoryLimit.value <= 0) return 0;
  return Math.ceil(syncDelHistoryTotal.value / syncDelHistoryLimit.value);
});

const syncDelPageRangeText = computed(() => {
  if (syncDelHistoryTotal.value <= 0) return "";
  const start = (syncDelHistoryPage.value - 1) * syncDelHistoryLimit.value + 1;
  const end = Math.min(syncDelHistoryPage.value * syncDelHistoryLimit.value, syncDelHistoryTotal.value);
  return `第 ${start} – ${end} 条，共 ${syncDelHistoryTotal.value} 条`;
});

const lastRefreshedTimeDisplaySyncDel = computed(() => {
  if (!lastRefreshedTimestampSyncDel.value) return "";
  const date = new Date(lastRefreshedTimestampSyncDel.value);
  return `更新于: ${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}:${date.getSeconds().toString().padStart(2, "0")}`;
});

const panTransferSelectItems = computed(() =>
  panTransferPathsList.value
    .map((p) => (p.path ? String(p.path).trim() : ""))
    .filter(Boolean)
    .map((path) => ({ title: path, value: path })),
);

const lastRefreshedTimeDisplayTransfer = computed(() => {
  if (!lastRefreshedTimestampTransfer.value) return "";
  const date = new Date(lastRefreshedTimestampTransfer.value);
  return `更新于: ${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}:${date.getSeconds().toString().padStart(2, "0")}`;
});

function parsePanTransferPaths(raw) {
  if (!raw || typeof raw !== "string") return [];
  return raw
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((path) => ({ path }));
}

async function loadTransferConfig() {
  transferConfigLoading.value = true;
  try {
    const data = await props.api.get(`plugin/${getPluginId()}/get_config`);
    if (data) {
      panTransferEnabled.value = Boolean(data.pan_transfer_enabled);
      panTransferPathsList.value = parsePanTransferPaths(data.pan_transfer_paths || "");
      const vals = panTransferSelectItems.value.map((x) => x.value);
      if (vals.length > 0) {
        if (!selectedManualPath.value || !vals.includes(selectedManualPath.value)) {
          selectedManualPath.value = vals[0];
        }
      } else {
        selectedManualPath.value = "";
      }
    }
    transferConfigLoaded.value = true;
    lastRefreshedTimestampTransfer.value = Date.now();
  } catch (err) {
    console.error("获取整理配置失败:", err);
    transferConfigLoaded.value = true;
  } finally {
    transferConfigLoading.value = false;
  }
}

function openManualTransferDialog() {
  const path = selectedManualPath.value?.trim();
  if (!path) return;
  manualTransferDialog.path = path;
  manualTransferDialog.loading = false;
  manualTransferDialog.result = null;
  manualTransferDialog.show = true;
}

function closeManualTransferDialog() {
  manualTransferDialog.show = false;
  manualTransferDialog.path = "";
  manualTransferDialog.loading = false;
  manualTransferDialog.result = null;
}

async function confirmManualTransfer() {
  if (!manualTransferDialog.path) return;
  manualTransferDialog.loading = true;
  manualTransferDialog.result = null;
  try {
    const result = await props.api.post(`plugin/${getPluginId()}/manual_transfer`, {
      path: manualTransferDialog.path,
    });
    if (result.code === 0) {
      manualTransferDialog.result = {
        type: "success",
        title: "整理任务已启动",
        message:
          result.msg || "整理任务已在后台启动，正在执行中。您可以在日志中查看详细进度。",
      };
    } else {
      manualTransferDialog.result = {
        type: "error",
        title: "启动失败",
        message: result.msg || "启动整理任务失败，请检查配置和网络连接。",
      };
    }
  } catch (error) {
    manualTransferDialog.result = {
      type: "error",
      title: "启动失败",
      message: `启动整理任务时发生错误：${error.message || error}`,
    };
  } finally {
    manualTransferDialog.loading = false;
  }
}

function calculateStoragePercentage(used, total) {
  if (!used || !total) return 0;
  const parseSize = (sizeStr) => {
    if (!sizeStr || typeof sizeStr !== "string") return 0;
    const value = parseFloat(sizeStr);
    if (Number.isNaN(value)) return 0;
    if (sizeStr.toUpperCase().includes("TB")) return value * 1024 * 1024;
    if (sizeStr.toUpperCase().includes("GB")) return value * 1024;
    if (sizeStr.toUpperCase().includes("MB")) return value;
    return value;
  };
  const usedValue = parseSize(used);
  const totalValue = parseSize(total);
  if (totalValue === 0) return 0;
  return Math.min(Math.max((usedValue / totalValue) * 100, 0), 100);
}

async function loadStrmHistory() {
  strmHistoryLoading.value = true;
  strmLoadError.value = null;
  try {
    const qs = new URLSearchParams({
      page: String(strmHistoryPage.value),
      limit: String(strmHistoryLimit.value),
    });
    const k = strmKindApplied.value?.trim();
    if (k) {
      qs.set("kind", k);
    }
    const result = await props.api.get(`plugin/${getPluginId()}/get_strm_sync_history?${qs.toString()}`);
    if (result && result.code === 0 && result.data) {
      strmHistoryItems.value = Array.isArray(result.data.items) ? result.data.items : [];
      strmHistoryTotal.value = result.data.total || 0;
      initialDataLoaded.value = true;
      lastRefreshedTimestamp.value = Date.now();
    } else {
      strmHistoryItems.value = [];
      strmHistoryTotal.value = 0;
      initialDataLoaded.value = true;
      throw new Error(result?.msg || "获取执行记录失败");
    }
  } catch (err) {
    console.error("获取 STRM 执行记录失败:", err);
    strmLoadError.value = err.message || "获取数据失败";
    initialDataLoaded.value = true;
  } finally {
    strmHistoryLoading.value = false;
  }
}

function applyStrmFilter() {
  const v = strmKindSelected.value;
  strmKindApplied.value = v != null && v !== "" ? String(v).trim() : "";
  strmHistoryPage.value = 1;
  loadStrmHistory();
}

function confirmDeleteStrm(row) {
  if (!row?.unique) return;
  deleteStrmConfirm.row = row;
  deleteStrmConfirm.show = true;
}

async function handleConfirmDeleteStrm() {
  const unique = deleteStrmConfirm.row?.unique;
  if (!unique) return;
  deleteStrmConfirm.show = false;
  deletingStrmId.value = unique;
  try {
    const response = await props.api.post(`plugin/${getPluginId()}/delete_strm_sync_history`, { key: unique });
    if (response && response.code === 0) {
      strmBannerMessage.value = response.msg || "删除成功";
      strmBannerType.value = "success";
      if (strmHistoryItems.value.length === 1 && strmHistoryPage.value > 1) {
        strmHistoryPage.value--;
      }
      await loadStrmHistory();
    } else {
      strmBannerMessage.value = response?.msg || "删除失败";
      strmBannerType.value = "error";
    }
  } catch (err) {
    console.error("删除 STRM 历史失败:", err);
    strmBannerMessage.value = `删除失败: ${err.message || "未知错误"}`;
    strmBannerType.value = "error";
  } finally {
    deletingStrmId.value = null;
    deleteStrmConfirm.row = null;
  }
}

function confirmDeleteAllStrm() {
  if (strmHistoryTotal.value === 0) return;
  deleteAllStrmConfirm.value = true;
}

async function handleConfirmDeleteAllStrm() {
  deleteAllStrmConfirm.value = false;
  deletingAllStrm.value = true;
  try {
    const response = await props.api.post(`plugin/${getPluginId()}/delete_all_strm_sync_history`);
    if (response && response.code === 0) {
      strmBannerMessage.value = response.msg || "已清空";
      strmBannerType.value = "success";
      strmHistoryPage.value = 1;
      await loadStrmHistory();
    } else {
      strmBannerMessage.value = response?.msg || "清空失败";
      strmBannerType.value = "error";
    }
  } catch (err) {
    console.error("清空 STRM 历史失败:", err);
    strmBannerMessage.value = `清空失败: ${err.message || "未知错误"}`;
    strmBannerType.value = "error";
  } finally {
    deletingAllStrm.value = false;
  }
}

async function loadSyncDelHistory() {
  syncDelHistoryLoading.value = true;
  syncDelLoadError.value = null;
  try {
    const response = await props.api.get(
      `plugin/${getPluginId()}/get_sync_del_history?page=${syncDelHistoryPage.value}&limit=${syncDelHistoryLimit.value}`,
    );
    if (response && response.code === 0 && response.data) {
      if (response.data.items && Array.isArray(response.data.items)) {
        syncDelHistory.value = response.data.items;
        syncDelHistoryTotal.value = response.data.total || 0;
      } else if (Array.isArray(response.data)) {
        syncDelHistory.value = response.data;
        syncDelHistoryTotal.value = response.data.length;
      } else {
        syncDelHistory.value = [];
        syncDelHistoryTotal.value = 0;
      }
      syncDelInitialLoaded.value = true;
      lastRefreshedTimestampSyncDel.value = Date.now();
    } else {
      syncDelHistory.value = [];
      syncDelHistoryTotal.value = 0;
      syncDelInitialLoaded.value = true;
      throw new Error(response?.msg || "获取同步删除历史失败");
    }
  } catch (err) {
    console.error("加载同步删除历史失败:", err);
    syncDelLoadError.value = err.message || "获取数据失败";
    syncDelHistory.value = [];
    syncDelHistoryTotal.value = 0;
    syncDelInitialLoaded.value = true;
  } finally {
    syncDelHistoryLoading.value = false;
  }
}

function confirmDeleteSyncDel(item) {
  if (!item?.unique) return;
  deleteSyncDelConfirm.item = item;
  deleteSyncDelConfirm.show = true;
}

async function handleConfirmDeleteSyncDel() {
  const unique = deleteSyncDelConfirm.item?.unique;
  if (!unique) return;
  deleteSyncDelConfirm.show = false;
  deletingSyncDelId.value = unique;
  try {
    const response = await props.api.post(`plugin/${getPluginId()}/delete_sync_del_history`, { key: unique });
    if (response && response.code === 0) {
      syncDelBannerMessage.value = response.msg || "删除成功";
      syncDelBannerType.value = "success";
      if (syncDelHistory.value.length === 1 && syncDelHistoryPage.value > 1) {
        syncDelHistoryPage.value--;
      }
      await loadSyncDelHistory();
    } else {
      syncDelBannerMessage.value = response?.msg || "删除失败";
      syncDelBannerType.value = "error";
    }
  } catch (err) {
    console.error("删除同步删除历史失败:", err);
    syncDelBannerMessage.value = `删除失败: ${err.message || "未知错误"}`;
    syncDelBannerType.value = "error";
  } finally {
    deletingSyncDelId.value = null;
    deleteSyncDelConfirm.item = null;
  }
}

function confirmDeleteAllSyncDel() {
  if (syncDelHistoryTotal.value === 0) return;
  deleteAllSyncDelConfirm.value = true;
}

async function handleConfirmDeleteAllSyncDel() {
  deleteAllSyncDelConfirm.value = false;
  deletingAllSyncDel.value = true;
  try {
    const response = await props.api.post(`plugin/${getPluginId()}/delete_all_sync_del_history`);
    if (response && response.code === 0) {
      syncDelBannerMessage.value = response.msg || "已清空";
      syncDelBannerType.value = "success";
      syncDelHistoryPage.value = 1;
      await loadSyncDelHistory();
    } else {
      syncDelBannerMessage.value = response?.msg || "清空失败";
      syncDelBannerType.value = "error";
    }
  } catch (err) {
    console.error("清空同步删除历史失败:", err);
    syncDelBannerMessage.value = `清空失败: ${err.message || "未知错误"}`;
    syncDelBannerType.value = "error";
  } finally {
    deletingAllSyncDel.value = false;
  }
}

async function fetchUserStorageStatus() {
  userInfo.loading = true;
  userInfo.error = null;
  storageInfo.loading = true;
  storageInfo.error = null;
  try {
    const response = await props.api.get(`plugin/${getPluginId()}/user_storage_status`);
    if (response && response.success) {
      if (response.user_info) {
        Object.assign(userInfo, response.user_info);
      } else {
        userInfo.error = "未能获取有效的用户信息。";
      }
      if (response.storage_info) {
        Object.assign(storageInfo, response.storage_info);
      } else {
        storageInfo.error = "未能获取有效的存储空间信息。";
      }
    } else {
      const errMsg = response?.error_message || "获取用户和存储信息失败。";
      userInfo.error = errMsg;
      storageInfo.error = errMsg;
      if (errMsg.includes("Cookie") || errMsg.includes("未配置")) {
        status.has_client = false;
      }
    }
  } catch (err) {
    console.error("获取用户/存储状态失败:", err);
    const msg = `请求用户/存储状态时出错: ${err.message || "未知网络错误"}`;
    userInfo.error = msg;
    storageInfo.error = msg;
  } finally {
    userInfo.loading = false;
    storageInfo.loading = false;
  }
}

async function fetchStatusData() {
  loading.value = true;
  error.value = null;
  try {
    const pluginId = getPluginId();
    const result = await props.api.get(`plugin/${pluginId}/get_status`);
    if (result && result.code === 0 && result.data) {
      status.enabled = Boolean(result.data.enabled);
      status.has_client = Boolean(result.data.has_client);
      status.running = Boolean(result.data.running);
      try {
        const configData = await props.api.get(`plugin/${pluginId}/get_config`);
        if (configData) {
          Object.assign(initialConfig, configData);
        }
      } catch (configErr) {
        console.error("获取配置失败:", configErr);
      }
      initialDataLoaded.value = true;
      if (status.has_client && initialConfig?.cookies) {
        await fetchUserStorageStatus();
      } else {
        userInfo.loading = false;
        storageInfo.loading = false;
        if (!initialConfig?.cookies) {
          userInfo.error = "请先配置115 Cookie。";
          storageInfo.error = "请先配置115 Cookie。";
        } else if (!status.has_client) {
          userInfo.error = "115客户端未连接或Cookie无效。";
          storageInfo.error = "115客户端未连接或Cookie无效。";
        }
      }
      lastRefreshedTimestamp.value = Date.now();
    } else {
      status.enabled = Boolean(initialConfig.enabled);
      status.has_client = Boolean(initialConfig.cookies && String(initialConfig.cookies).trim() !== "");
      status.running = false;
      initialDataLoaded.value = true;
      try {
        const configData = await props.api.get(`plugin/${pluginId}/get_config`);
        if (configData) {
          Object.assign(initialConfig, configData);
        }
      } catch (configErr) {
        console.error("获取配置失败:", configErr);
      }
      userInfo.loading = false;
      storageInfo.loading = false;
      throw new Error(result?.msg || "获取状态失败");
    }
  } catch (err) {
    error.value = `获取状态失败: ${err.message || "未知错误"}`;
    console.error("获取状态失败:", err);
    initialDataLoaded.value = true;
  } finally {
    loading.value = false;
  }
}

const lastRefreshedTimeDisplay = computed(() => {
  if (!lastRefreshedTimestamp.value) return "";
  const date = new Date(lastRefreshedTimestamp.value);
  return `更新于: ${date.getHours().toString().padStart(2, "0")}:${date
    .getMinutes()
    .toString()
    .padStart(2, "0")}:${date.getSeconds().toString().padStart(2, "0")}`;
});

onMounted(() => {
  ensureSentryInitialized();
  if (panelKey.value === "strm") {
    loadStrmHistory();
    if (props.refreshInterval > 0) {
      refreshTimer = setInterval(loadStrmHistory, props.refreshInterval * 1000);
    }
  } else if (panelKey.value === "status") {
    fetchStatusData();
    if (props.refreshInterval > 0) {
      refreshTimer = setInterval(fetchStatusData, props.refreshInterval * 1000);
    }
  } else if (panelKey.value === "sync_del") {
    loadSyncDelHistory();
    if (props.refreshInterval > 0) {
      refreshTimer = setInterval(loadSyncDelHistory, props.refreshInterval * 1000);
    }
  } else if (panelKey.value === "manual_transfer") {
    loadTransferConfig();
  }
});

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
});
</script>

<style scoped>
.dashboard-widget--strm {
  height: 100%;
  width: 100%;
  min-height: 420px;
}

.dashboard-widget--status {
  height: 100%;
  width: 100%;
  min-height: 420px;
}

.dashboard-widget--sync-del {
  height: 100%;
  width: 100%;
  min-height: 420px;
}

.sync-del-dash-card__body {
  min-height: 280px;
  max-height: min(70vh, 640px);
}

.sync-del-list {
  overflow-y: auto;
  padding-right: 4px;
}

.sync-del-path {
  word-break: break-word;
  line-height: 1.45;
}

.dashboard-widget--transfer-mini {
  height: 100%;
  width: 100%;
  min-height: 200px;
}

.transfer-mini-card__body {
  min-height: 100px;
}

.strm-dash-card,
.status-dash-card {
  border-radius: 16px !important;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.78) !important;
  backdrop-filter: blur(18px) saturate(160%) !important;
  -webkit-backdrop-filter: blur(18px) saturate(160%) !important;
  box-shadow:
    0 8px 28px rgba(91, 207, 250, 0.15),
    0 2px 8px rgba(245, 171, 185, 0.1),
    inset 0 1px 0 rgba(255, 255, 255, 0.75) !important;
  border: 1px solid rgba(255, 255, 255, 0.45) !important;
}

.status-dash-row :deep(.v-col.d-flex) {
  align-self: stretch;
}

.status-inner-card {
  min-block-size: 100%;
  border-radius: 12px !important;
}

.strm-dash-card__body {
  min-height: 320px;
  max-height: min(70vh, 640px);
}

.strm-dash-list {
  overflow-y: auto;
  padding-right: 4px;
}

.strm-kind-field {
  max-width: 280px;
  flex: 1 1 200px;
  min-width: 0;
}

.strm-dash-item {
  border-radius: 12px;
  border-inline-start: 3px solid rgb(var(--v-theme-primary));
  transition: box-shadow 0.2s ease;
}

.strm-dash-item--fail {
  border-inline-start-color: rgb(var(--v-theme-error));
}

.strm-dash-item:hover,
.strm-history-item:hover {
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.strm-history-item {
  border-inline-start: 3px solid rgb(var(--v-theme-primary));
  transition: box-shadow 0.2s ease;
}

.strm-history-item--fail {
  border-inline-start-color: rgb(var(--v-theme-error));
}

.strm-section-label {
  letter-spacing: 0.02em;
}

.strm-extra-line {
  word-break: break-word;
}

.refresh-actions {
  background: linear-gradient(135deg,
      rgba(91, 207, 250, 0.1) 0%,
      rgba(245, 171, 185, 0.08) 100%) !important;
  border-top: 1px solid rgba(255, 255, 255, 0.5) !important;
}

@media (max-width: 768px) {
  .dashboard-widget--strm {
    min-height: 360px;
  }

  .strm-dash-card__body {
    max-height: min(65vh, 520px);
  }

  .dashboard-widget--sync-del {
    min-height: 360px;
  }

  .sync-del-dash-card__body {
    max-height: min(65vh, 520px);
  }
}
</style>
