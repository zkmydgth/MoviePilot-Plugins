package handler

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/config"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/hdhive"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/store"
)

// Handler serves OAuth broker routes
type Handler struct {
	cfg   config.Config
	store store.Store
	hive  *hdhive.Client
}

// New creates a Handler
func New(cfg config.Config, st store.Store, hive *hdhive.Client) *Handler {
	return &Handler{cfg: cfg, store: st, hive: hive}
}

// OAuthStart GET /oauth/hdhive/start
func (h *Handler) OAuthStart(c *gin.Context) {
	instanceKey := strings.TrimSpace(c.Query("instance_key"))
	scope := strings.TrimSpace(c.Query("scope"))
	responseMode := strings.TrimSpace(c.Query("response_mode"))
	if instanceKey == "" {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "code": "MISSING_INSTANCE_KEY"})
		return
	}
	if scope == "" {
		scope = "query unlock"
	}
	// 优先使用 redirect（query）模式，避免部分环境下 postMessage 不投递导致一直 pending
	if responseMode == "" {
		responseMode = "query"
	}
	state, err := randomState()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"success": false, "code": "STATE_ERROR"})
		return
	}
	expires := time.Now().Add(h.cfg.StateTTL)
	if err := h.store.PutOAuthState(c.Request.Context(), state, store.OAuthState{
		InstanceKey: instanceKey,
		Scope:       scope,
		RedirectURI: h.cfg.RedirectURI,
		ExpiresAt:   expires,
	}); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"success": false, "code": "STORE_ERROR"})
		return
	}
	q := url.Values{}
	q.Set("client_id", h.cfg.ClientID)
	q.Set("redirect_uri", h.cfg.RedirectURI)
	q.Set("scope", scope)
	q.Set("state", state)
	if responseMode != "" {
		q.Set("response_mode", responseMode)
	}
	authorizeURL := h.cfg.HDHiveBase + h.cfg.AuthorizePath + "?" + q.Encode()
	c.JSON(http.StatusOK, gin.H{
		"success":       true,
		"authorize_url": authorizeURL,
		"state":         state,
		"redirect_uri":  h.cfg.RedirectURI,
		"response_mode": responseMode,
		"instance_key":  instanceKey,
	})
}

// OAuthCallback GET /oauth/hdhive/callback
//
// HDHive 在用户完成授权后会跳转到 redirect_uri（本服务），并携带 code/state
// 本页面会将 code/state 通过 postMessage 发回 opener（MoviePilot 插件页），随后尝试关闭窗口
func (h *Handler) OAuthCallback(c *gin.Context) {
	code := strings.TrimSpace(c.Query("code"))
	state := strings.TrimSpace(c.Query("state"))
	if code == "" || state == "" {
		c.Header("Content-Type", "text/html; charset=utf-8")
		c.String(http.StatusBadRequest, `<!doctype html><html><body><h3>缺少 code/state</h3></body></html>`)
		return
	}

	payload, _ := json.Marshal(map[string]string{
		"code":  code,
		"state": state,
	})

	c.Header("Content-Type", "text/html; charset=utf-8")
	c.String(http.StatusOK, fmt.Sprintf(`<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>HDHive OAuth</title>
  </head>
  <body>
    <h3>授权成功</h3>
    <p>正在返回 MoviePilot…</p>
    <script>
      (function () {
        const payload = %s;
        try {
          if (window.opener && window.opener.postMessage) {
            window.opener.postMessage(payload, "*");
          }
          if (window.parent && window.parent !== window && window.parent.postMessage) {
            window.parent.postMessage(payload, "*");
          }
        } catch (e) {}
        setTimeout(function () {
          try { window.close(); } catch (e) {}
        }, 50);
      })();
    </script>
  </body>
</html>`, payload))
}

type exchangeRequest struct {
	InstanceKey  string `json:"instance_key"`
	Code         string `json:"code"`
	State        string `json:"state"`
	RedirectURI  string `json:"redirect_uri"`
}

// OAuthExchange POST /oauth/hdhive/exchange
func (h *Handler) OAuthExchange(c *gin.Context) {
	var req exchangeRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "code": "INVALID_BODY"})
		return
	}
	sess, err := h.store.GetAndDeleteOAuthState(c.Request.Context(), req.State)
	if err != nil || sess.InstanceKey != req.InstanceKey {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "code": "INVALID_STATE"})
		return
	}
	redirectURI := req.RedirectURI
	if redirectURI == "" {
		redirectURI = h.cfg.RedirectURI
	}
	tokens, err := h.hive.ExchangeCode(c.Request.Context(), req.Code, redirectURI)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"success": false, "code": "EXCHANGE_FAILED", "message": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": tokens})
}

type refreshRequest struct {
	InstanceKey   string `json:"instance_key"`
	RefreshToken  string `json:"refresh_token"`
}

// OAuthRefresh POST /oauth/hdhive/refresh
func (h *Handler) OAuthRefresh(c *gin.Context) {
	var req refreshRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "code": "INVALID_BODY"})
		return
	}
	if req.InstanceKey == "" || req.RefreshToken == "" {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "code": "MISSING_FIELDS"})
		return
	}
	tokens, err := h.hive.RefreshToken(c.Request.Context(), req.RefreshToken)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"success": false, "code": "REFRESH_FAILED", "message": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"success": true, "data": tokens})
}

type revokeRequest struct {
	InstanceKey  string `json:"instance_key"`
	RefreshToken string `json:"refresh_token"`
}

// OAuthRevoke POST /oauth/hdhive/revoke
func (h *Handler) OAuthRevoke(c *gin.Context) {
	var req revokeRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"success": false, "code": "INVALID_BODY"})
		return
	}
	if req.RefreshToken != "" {
		_ = h.hive.RevokeToken(c.Request.Context(), req.RefreshToken)
	}
	c.JSON(http.StatusOK, gin.H{"success": true})
}

// ProxyOpen handles /proxy/open/*path
func (h *Handler) ProxyOpen(c *gin.Context) {
	bearer := strings.TrimSpace(c.GetHeader("Authorization"))
	if strings.HasPrefix(strings.ToLower(bearer), "bearer ") {
		bearer = strings.TrimSpace(bearer[7:])
	}
	if bearer == "" {
		bearer = strings.TrimSpace(c.GetHeader("X-Access-Token"))
	}
	path := c.Param("path")
	if path == "" {
		path = "/"
	} else if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	body, _ := io.ReadAll(c.Request.Body)
	status, data, hdrs, err := h.hive.ProxyOpen(
		c.Request.Context(),
		c.Request.Method,
		path,
		c.Request.URL.RawQuery,
		body,
		bearer,
	)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"success": false, "code": "PROXY_ERROR", "message": err.Error()})
		return
	}
	for k, vals := range hdrs {
		if strings.EqualFold(k, "Content-Length") {
			continue
		}
		for _, v := range vals {
			c.Header(k, v)
		}
	}
	c.Data(status, hdrs.Get("Content-Type"), data)
}

func randomState() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// Health GET /health
func (h *Handler) Health(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

// ValidateConfig returns error if required env is missing
func ValidateConfig(cfg config.Config) error {
	if cfg.ClientID == "" {
		return fmt.Errorf("HDHIVE_CLIENT_ID is required")
	}
	if cfg.AppSecret == "" {
		return fmt.Errorf("HDHIVE_APP_SECRET is required")
	}
	if cfg.RedirectURI == "" {
		return fmt.Errorf("HDHIVE_REDIRECT_URI is required")
	}
	return nil
}
