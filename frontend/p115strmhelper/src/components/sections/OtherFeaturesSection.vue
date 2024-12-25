<template>
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
      <v-tab value="tab-hdhive-checkin" class="sub-tab">
        <v-icon size="small" start>mdi-calendar-check</v-icon>HDHive 签到
      </v-tab>
      <v-tab value="tab-utility" class="sub-tab">
        <v-icon size="small" start>mdi-toolbox</v-icon>实用工具
      </v-tab>
    </v-tabs>
    <v-divider></v-divider>
    <v-window v-model="otherSubTab" :touch="false" class="tab-window">
      <v-window-item value="tab-sync-del">
        <v-card-text>
          <v-row>
            <v-col cols="12" md="3">
              <v-switch v-model="config.sync_del_enabled" label="启用同步删除" color="warning" density="compact"></v-switch>
            </v-col>
            <v-col cols="12" md="3">
              <v-switch v-model="config.sync_del_notify" label="发送通知" color="success" density="compact"></v-switch>
            </v-col>
            <v-col cols="12" md="3">
              <v-switch v-model="config.sync_del_source" label="删除源文件" color="error" density="compact"></v-switch>
            </v-col>
            <v-col cols="12" md="3">
              <v-switch v-model="config.sync_del_p115_force_delete_files" label="强制删除文件" color="warning"
                density="compact"></v-switch>
            </v-col>
          </v-row>

          <v-row>
            <v-col cols="12" md="6">
              <v-switch v-model="config.sync_del_remove_versions" label="开启多版本删除" color="info" density="compact" chips
                closable-chips hint="请查看下方警告提示了解详细说明" persistent-hint></v-switch>
            </v-col>
            <v-col cols="12" md="6">
              <v-select v-model="config.sync_del_mediaservers" label="媒体服务器" :items="embyMediaservers" multiple
                density="compact" chips closable-chips hint="用于获取TMDB ID，仅支持Emby" persistent-hint></v-select>
            </v-col>
          </v-row>

          <v-row>
            <v-col cols="12">
              <div class="d-flex flex-column">
                <div v-for="(path, index) in syncDelLibraryPaths" :key="`sync-del-${index}`"
                  class="mb-3 pa-3 border rounded">
                  <v-row dense>
                    <v-col cols="12" md="4">
                      <v-text-field v-model="path.mediaserver" label="媒体服务器STRM路径" density="compact" variant="outlined"
                        hint="例如：/media/strm" persistent-hint></v-text-field>
                    </v-col>
                    <v-col cols="12" md="4">
                      <v-text-field v-model="path.moviepilot" label="MoviePilot路径" density="compact" variant="outlined"
                        hint="例如：/mnt/strm" persistent-hint append-icon="mdi-folder-home"
                        @click:append="openDirSelector(index, 'local', 'syncDelLibrary', 'moviepilot')"></v-text-field>
                    </v-col>
                    <v-col cols="12" md="4">
                      <v-text-field v-model="path.p115" label="115网盘媒体库路径" density="compact" variant="outlined"
                        hint="例如：/影视" persistent-hint append-icon="mdi-cloud"
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
                <v-btn size="small" prepend-icon="mdi-plus" variant="outlined" class="mt-2 align-self-start"
                  @click="addPath('syncDelLibrary')">
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
                <v-text-field v-model="channel.name" label="频道名称" placeholder="例如：爱影115资源分享频道" density="compact"
                  variant="outlined" hide-details class="mr-3"></v-text-field>
                <v-text-field v-model="channel.id" label="频道ID" placeholder="例如：ayzgzf" density="compact"
                  variant="outlined" hide-details class="mr-3"></v-text-field>
                <v-btn icon size="small" color="error" variant="tonal" @click="removeTgChannel(index)" title="删除此频道">
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

          <v-card variant="outlined" class="mt-4">
            <v-card-item>
              <v-card-title class="d-flex align-center">
                <v-icon start>mdi-api</v-icon>
                <span class="text-h6">HDHive Open API</span>
              </v-card-title>
            </v-card-item>
            <v-card-text>
              <v-text-field v-model="config.hdhive_api_key" label="HDHive API Key" placeholder="在 HDHive 控制台获取 API Key"
                density="compact" variant="outlined" type="password" autocomplete="new-password" clearable />
            </v-card-text>
          </v-card>

          <v-alert type="info" variant="tonal" density="compact" class="mt-6" icon="mdi-information">
            <div class="text-body-2 mb-1"><strong>频道搜索说明（/sh）</strong></div>
            <div class="text-caption">
              <div class="mb-1">• 请至少配置 <strong>Telegram 频道</strong> 或 <strong>HDHive API Key</strong> 之一，否则无法使用
                <code>/sh</code> 检索资源
              </div>
              <div>• HDHive 与 TG 结果会合并展示；HDHive 仅展示 115 网盘类资源，积分需求在列表中可见，确认转存时才解锁</div>
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
              <v-switch v-model="config.clear_receive_path_enabled" label="清空最近接收目录" color="error"></v-switch>
            </v-col>
            <v-col cols="12" md="3">
              <v-text-field v-model="config.password" label="115访问密码" hint="115网盘安全密码" persistent-hint type="password"
                density="compact" variant="outlined" hide-details="auto"></v-text-field>
            </v-col>
            <v-col cols="12" md="3">
              <VCronField v-model="config.cron_clear" label="清理周期" hint="设置清理任务的执行周期" persistent-hint density="compact">
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
      <v-window-item value="tab-hdhive-checkin">
        <v-card-text>
          <v-row>
            <v-col cols="12" md="6">
              <v-text-field v-model="config.hdhive_checkin_username" label="HDHive账户" density="compact"
                variant="outlined" autocomplete="username" />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field v-model="config.hdhive_checkin_password" label="HDHive密码" type="password" density="compact"
                variant="outlined" autocomplete="new-password" />
            </v-col>
          </v-row>
          <v-row>
            <v-col cols="12" md="4">
              <v-switch v-model="config.hdhive_checkin_daily_enabled" label="每日签到" color="primary" density="compact" />
            </v-col>
            <v-col cols="12" md="4">
              <v-switch v-model="config.hdhive_checkin_gamble_enabled" label="赌狗签到" color="warning" density="compact" />
            </v-col>
            <v-col cols="12" md="4">
              <v-text-field v-model="config.hdhive_checkin_time_range" label="签到随机时间段"
                hint="格式 HH:MM-HH:MM，例如 06:30-09:45" persistent-hint density="compact" variant="outlined" />
            </v-col>
          </v-row>
          <v-alert type="info" variant="tonal" density="compact" class="mt-3" icon="mdi-information">
            <div class="text-body-2 mb-1"><strong>HDHive 签到</strong></div>
            <div class="text-caption">
              <div class="mb-1">• 请填写 HDHive 账户与密码；每日签到与赌狗签到<strong>只能二选一</strong>开启</div>
              <div class="mb-1">• 启用后将在设定时间窗口内<strong>每天随机一刻</strong>执行一次登录并签到</div>
              <div>• 也可在 Telegram 等渠道发送 <code>/hdhivechin</code> 手动触发签到</div>
            </div>
          </v-alert>
        </v-card-text>
      </v-window-item>
      <v-window-item value="tab-utility">
        <v-card-text class="px-2">
          <!-- 功能 1: 分享STRM覆盖大小检查 -->
          <v-card variant="outlined" class="mb-4" density="compact">
            <v-card-title class="d-flex align-center py-2 px-4 bg-primary-lighten-5">
              <v-icon size="small" color="primary" class="mr-2">mdi-file-compare</v-icon>
              <span class="text-subtitle-2 font-weight-medium">分享STRM覆盖大小检查</span>
            </v-card-title>
            <v-divider></v-divider>
            <v-card-text class="py-3 px-4">
              <v-row align="center">
                <v-col cols="12" md="4">
                  <v-switch v-model="config.share_strm_overwrite_check_enabled" label="启用功能" color="primary"
                    density="compact" hide-details></v-switch>
                </v-col>
                <v-col cols="12" md="8">
                  <v-alert type="info" variant="tonal" density="compact" class="text-caption">
                    解决按大小覆盖时 STRM 文件被误判为小文件的问题<br>
                    优先使用生成时的缓存数据，未命中时使用 ffprobe 探测实际大小
                  </v-alert>
                </v-col>
              </v-row>
            </v-card-text>
          </v-card>

          <!-- 功能 2: 自动删除低质量源文件 -->
          <v-card variant="outlined" class="mb-4" density="compact">
            <v-card-title class="d-flex align-center py-2 px-4 bg-warning-lighten-5">
              <v-icon size="small" color="warning" class="mr-2">mdi-delete-alert</v-icon>
              <span class="text-subtitle-2 font-weight-medium">自动删除低质量源文件</span>
            </v-card-title>
            <v-divider></v-divider>
            <v-card-text class="py-3 px-4">
              <v-row align="center">
                <v-col cols="12" md="4">
                  <v-switch v-model="config.auto_delete_inferior_source_enabled" label="启用功能" color="warning"
                    density="compact" hide-details></v-switch>
                </v-col>
                <v-col cols="12" md="8">
                  <v-alert type="warning" variant="tonal" density="compact" class="text-caption">
                    <strong>注意：</strong>按文件大小整理失败时（媒体库已存在更高质量文件），自动删除源文件<br>
                    支持所有存储类型：本地、115网盘、CloudDrive
                  </v-alert>
                </v-col>
              </v-row>
            </v-card-text>
          </v-card>

          <!-- 功能 3: 媒体库已存在时拦截整理 -->
          <v-card variant="outlined" class="mb-4" density="compact">
            <v-card-title class="d-flex align-center py-2 px-4 bg-error-lighten-5">
              <v-icon size="small" color="error" class="mr-2">mdi-cancel</v-icon>
              <span class="text-subtitle-2 font-weight-medium">媒体库已存在时拦截整理</span>
            </v-card-title>
            <v-divider></v-divider>
            <v-card-text class="py-3 px-4">
              <v-row align="center">
                <v-col cols="12" md="4">
                  <v-switch v-model="config.transfer_intercept_exists_enabled" label="启用功能" color="error"
                    density="compact" hide-details></v-switch>
                </v-col>
                <v-col cols="12" md="8">
                  <v-alert type="error" variant="tonal" density="compact" class="text-caption">
                    整理前查询所有媒体服务器，若已存在则立即终止并记录失败<br>
                    <strong>生效条件：</strong>目标路径尚无同名文件时触发（首次入库场景）<br>
                    若目标文件已存在，覆盖检查会优先介入，本功能不会触发<br>
                    <strong>注意：</strong>每次整理均会请求媒体服务器，需确保媒体已完成刮削入库
                  </v-alert>
                </v-col>
              </v-row>
            </v-card-text>
          </v-card>
        </v-card-text>
      </v-window-item>
    </v-window>
  </v-card-text>
</template>

<script setup>
import { ref, inject, watch } from 'vue';

const otherSubTab = ref('tab-sync-del');

const config = inject('config');
const embyMediaservers = inject('embyMediaservers');
const syncDelLibraryPaths = inject('syncDelLibraryPaths');
const tgChannels = inject('tgChannels');
const addPath = inject('addPath');
const removePath = inject('removePath');
const openDirSelector = inject('openDirSelector');
const addTgChannel = inject('addTgChannel');
const removeTgChannel = inject('removeTgChannel');
const openImportDialog = inject('openImportDialog');

watch(
  () => config.hdhive_checkin_daily_enabled,
  (v) => {
    if (v) {
      config.hdhive_checkin_gamble_enabled = false;
    }
  },
);

watch(
  () => config.hdhive_checkin_gamble_enabled,
  (v) => {
    if (v) {
      config.hdhive_checkin_daily_enabled = false;
    }
  },
);
</script>
