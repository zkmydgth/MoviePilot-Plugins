package config

import (
	"os"
	"strconv"
	"time"
)

// Config holds broker environment configuration
type Config struct {
	ListenAddr    string
	ClientID      string
	AppSecret     string
	RedirectURI   string
	HDHiveBase    string
	StateTTL      time.Duration
	AuthorizePath string
	TokenPath     string
	RefreshPath   string
	RevokePath    string
	OpenAPIBase   string
}

// Load reads configuration from environment variables
func Load() Config {
	ttlMin := 10
	if v := os.Getenv("OAUTH_STATE_TTL_MINUTES"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			ttlMin = n
		}
	}
	base := getenv("HDHIVE_BASE_URL", "https://hdhive.com")
	return Config{
		ListenAddr:    getenv("LISTEN_ADDR", ":8080"),
		ClientID:      os.Getenv("HDHIVE_CLIENT_ID"),
		AppSecret:     os.Getenv("HDHIVE_APP_SECRET"),
		RedirectURI:   os.Getenv("HDHIVE_REDIRECT_URI"),
		HDHiveBase:    base,
		StateTTL:      time.Duration(ttlMin) * time.Minute,
		AuthorizePath: "/openapi/authorize",
		TokenPath:     "/api/public/openapi/oauth/token",
		RefreshPath:   "/api/public/openapi/oauth/refresh",
		RevokePath:    "/api/public/openapi/oauth/revoke",
		OpenAPIBase:   base + "/api/open",
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
