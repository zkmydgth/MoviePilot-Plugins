package handler_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/config"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/handler"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/hdhive"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/store"
)

func TestOAuthExchangeInvalidState(t *testing.T) {
	gin.SetMode(gin.TestMode)
	cfg := config.Config{
		ClientID:    "app_test",
		AppSecret:   "secret",
		RedirectURI: "https://broker.example/oauth/callback",
		HDHiveBase:  "https://hdhive.com",
		StateTTL:    10 * time.Minute,
	}
	mem := store.NewMemory()
	hive := hdhive.NewClient(
		cfg.HDHiveBase,
		"/api/public/openapi/oauth/token",
		"/api/public/openapi/oauth/refresh",
		"/api/public/openapi/oauth/revoke",
		cfg.HDHiveBase+"/api/open",
		cfg.AppSecret,
	)
	h := handler.New(cfg, mem, hive)

	r := gin.New()
	r.POST("/oauth/hdhive/exchange", h.OAuthExchange)

	body, _ := json.Marshal(map[string]string{
		"instance_key": "inst1",
		"code":         "code1",
		"state":        "missing",
	})
	req := httptest.NewRequest(http.MethodPost, "/oauth/hdhive/exchange", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d body=%s", w.Code, w.Body.String())
	}
}

func TestOAuthStartReturnsAuthorizeURL(t *testing.T) {
	gin.SetMode(gin.TestMode)
	cfg := config.Config{
		ClientID:      "app_test",
		AppSecret:     "secret",
		RedirectURI:   "https://broker.example/oauth/callback",
		HDHiveBase:    "https://hdhive.com",
		AuthorizePath: "/openapi/authorize",
		StateTTL:      10 * time.Minute,
	}
	mem := store.NewMemory()
	hive := hdhive.NewClient(
		cfg.HDHiveBase,
		"/api/public/openapi/oauth/token",
		"/api/public/openapi/oauth/refresh",
		"/api/public/openapi/oauth/revoke",
		cfg.HDHiveBase+"/api/open",
		cfg.AppSecret,
	)
	h := handler.New(cfg, mem, hive)

	r := gin.New()
	r.GET("/oauth/hdhive/start", h.OAuthStart)

	req := httptest.NewRequest(http.MethodGet, "/oauth/hdhive/start?instance_key=inst1&scope=query+unlock", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}
	var resp map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &resp); err != nil {
		t.Fatal(err)
	}
	if resp["authorize_url"] == "" {
		t.Fatal("missing authorize_url")
	}
}
