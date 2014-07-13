package store

import (
	"context"
	"time"
)

// OAuthState holds short-lived OAuth session data
type OAuthState struct {
	InstanceKey  string
	Scope        string
	RedirectURI  string
	ExpiresAt    time.Time
}

// Store persists short-lived OAuth state
type Store interface {
	PutOAuthState(ctx context.Context, state string, sess OAuthState) error
	GetAndDeleteOAuthState(ctx context.Context, state string) (*OAuthState, error)
	StartJanitor(ctx context.Context, interval time.Duration)
}
