package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/gin-gonic/gin"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/config"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/handler"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/hdhive"
	"github.com/jxxghp/MoviePilot-Plugins/services/hdhive-auth-broker/internal/store"
)

func main() {
	cfg := config.Load()
	if err := handler.ValidateConfig(cfg); err != nil {
		log.Fatal(err)
	}

	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	mem := store.NewMemory()
	mem.StartJanitor(ctx, 0)

	hive := hdhive.NewClient(
		cfg.HDHiveBase,
		cfg.TokenPath,
		cfg.RefreshPath,
		cfg.RevokePath,
		cfg.OpenAPIBase,
		cfg.AppSecret,
	)
	h := handler.New(cfg, mem, hive)

	if os.Getenv("GIN_MODE") == "" {
		gin.SetMode(gin.ReleaseMode)
	}
	r := gin.New()
	r.Use(gin.Recovery())
	r.GET("/health", h.Health)

	oauth := r.Group("/oauth/hdhive")
	{
		oauth.GET("/start", h.OAuthStart)
		oauth.GET("/callback", h.OAuthCallback)
		oauth.POST("/exchange", h.OAuthExchange)
		oauth.POST("/refresh", h.OAuthRefresh)
		oauth.POST("/revoke", h.OAuthRevoke)
	}

	r.Any("/proxy/open/*path", h.ProxyOpen)

	log.Printf("hdhive-auth-broker listening on %s", cfg.ListenAddr)
	go func() {
		if err := r.Run(cfg.ListenAddr); err != nil {
			log.Fatal(err)
		}
	}()
	<-ctx.Done()
	log.Println("shutting down")
}
