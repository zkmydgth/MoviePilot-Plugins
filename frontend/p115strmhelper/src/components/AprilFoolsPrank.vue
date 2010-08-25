<template>
  <div v-if="showCrash" class="april-fools-crash" role="alert" @pointerdown.prevent="onCrashClick">
    <div class="april-fools-crash__panel">
      <div class="april-fools-crash__icon" aria-hidden="true">!</div>
      <h1 class="april-fools-crash__title">致命错误</h1>
      <p class="april-fools-crash__line">插件已经崩溃</p>
      <p class="april-fools-crash__line april-fools-crash__line--warn">您的 115 账户已被封禁</p>
      <p class="april-fools-crash__hint">点击任意处尝试恢复</p>
    </div>
  </div>

  <v-dialog v-model="showHappy" max-width="420" @click:outside="completePrank">
    <v-card class="rounded-lg pa-2" @click.stop>
      <v-card-text class="text-center text-h6 font-weight-medium pt-6 pb-2">
        愚人节快乐！
      </v-card-text>
      <v-card-text class="text-center text-body-2 text-medium-emphasis pb-2">
        刚才只是玩笑，插件与账户均正常
      </v-card-text>
      <v-card-actions class="px-4 pb-4">
        <v-btn block color="primary" variant="flat" rounded="lg" @click="completePrank">
          好的
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue';

const STORAGE_KEY = 'p115strmhelper_april_fools_done_date';

const FORCE_STORAGE_KEY = 'p115strmhelper_april_fools_force';

const props = defineProps({
  /**
   * 插件根容器：父级模板传入的 ref 会被自动解包，此处为 HTMLElement 或 null（少数构建下也可能是 Ref）
   */
  captureRoot: {
    type: [Object, HTMLElement],
    default: null,
  },
});

/**
 * 父模板 :capture-root="refObj" 时 Vue 会传入解包后的 DOM；直接传 ref 对象时取 .value
 */
function resolveCaptureRootEl() {
  const p = props.captureRoot;
  if (p == null) {
    return null;
  }
  if (typeof HTMLElement !== 'undefined' && p instanceof HTMLElement) {
    return p;
  }
  if (typeof p === 'object' && p !== null && 'value' in p) {
    const v = p.value;
    return typeof HTMLElement !== 'undefined' && v instanceof HTMLElement ? v : null;
  }
  return null;
}

function localDateKey() {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function isAprilFoolsDay() {
  const d = new Date();
  return d.getMonth() === 3 && d.getDate() === 1;
}

/** 4 月 1 日，或 localStorage 设 p115strmhelper_april_fools_force=1 便于联调 */
function shouldRunPrank() {
  if (isAprilFoolsDay()) {
    return true;
  }
  try {
    return localStorage.getItem(FORCE_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

const showCrash = ref(false);
const showHappy = ref(false);
let abortCapture = null;
/** 避免 onMounted 与 watch 重复注册导致 abort 掉尚未触发的监听 */
let attachedToEl = null;

const completePrank = () => {
  showHappy.value = false;
  showCrash.value = false;
  try {
    localStorage.setItem(STORAGE_KEY, localDateKey());
  } catch {
    /* ignore quota / private mode */
  }
};

const onCrashClick = () => {
  showCrash.value = false;
  showHappy.value = true;
};

function attachFirstClickListener() {
  if (!shouldRunPrank()) {
    return;
  }
  const today = localDateKey();
  let forced = false;
  try {
    forced = localStorage.getItem(FORCE_STORAGE_KEY) === '1';
  } catch {
    forced = false;
  }
  if (!forced) {
    try {
      if (localStorage.getItem(STORAGE_KEY) === today) {
        return;
      }
    } catch {
      /* still allow prank if storage unavailable */
    }
  }

  const el = resolveCaptureRootEl();
  if (!el) {
    return;
  }
  if (attachedToEl === el) {
    return;
  }

  if (abortCapture) {
    abortCapture.abort();
    abortCapture = null;
  }
  attachedToEl = el;

  const ac = new AbortController();
  abortCapture = ac;

  const onFirstPointerDown = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (typeof e.stopImmediatePropagation === 'function') {
      e.stopImmediatePropagation();
    }
    showCrash.value = true;
  };

  el.addEventListener('pointerdown', onFirstPointerDown, {
    capture: true,
    passive: false,
    once: true,
    signal: ac.signal,
  });
}

onMounted(() => {
  nextTick(attachFirstClickListener);
});

watch(
  () => resolveCaptureRootEl(),
  () => nextTick(attachFirstClickListener),
  { flush: 'post', immediate: true },
);

onBeforeUnmount(() => {
  if (abortCapture) {
    abortCapture.abort();
    abortCapture = null;
  }
  attachedToEl = null;
});
</script>

<style scoped>
.april-fools-crash {
  position: fixed;
  inset: 0;
  z-index: 100000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  box-sizing: border-box;
  background: #0d1117;
  color: #f0f6fc;
  font-family: ui-monospace, 'Cascadia Code', 'SF Mono', Menlo, Consolas, monospace;
  cursor: default;
  user-select: none;
  -webkit-user-select: none;
}

.april-fools-crash__panel {
  max-width: 420px;
  text-align: center;
}

.april-fools-crash__icon {
  width: 56px;
  height: 56px;
  margin: 0 auto 16px;
  border-radius: 50%;
  background: #da3633;
  color: #fff;
  font-size: 32px;
  font-weight: 800;
  line-height: 56px;
}

.april-fools-crash__title {
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0 0 20px;
  letter-spacing: 0.02em;
}

.april-fools-crash__line {
  margin: 0 0 12px;
  font-size: 0.95rem;
  line-height: 1.5;
  color: #8b949e;
}

.april-fools-crash__line--warn {
  color: #ff7b72;
  font-weight: 600;
}

.april-fools-crash__hint {
  margin: 28px 0 0;
  font-size: 0.8rem;
  color: #484f58;
}
</style>
