package store

import (
	"context"
	"errors"
	"sync"
	"time"
)

var ErrStateNotFound = errors.New("oauth state not found")

// Memory is an in-process OAuth state store
type Memory struct {
	mu    sync.RWMutex
	items map[string]OAuthState
}

// NewMemory creates an empty memory store
func NewMemory() *Memory {
	return &Memory{items: make(map[string]OAuthState)}
}

// PutOAuthState saves state with TTL from sess.ExpiresAt
func (m *Memory) PutOAuthState(_ context.Context, state string, sess OAuthState) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.items[state] = sess
	return nil
}

// GetAndDeleteOAuthState loads and removes state atomically
func (m *Memory) GetAndDeleteOAuthState(_ context.Context, state string) (*OAuthState, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	sess, ok := m.items[state]
	if !ok {
		return nil, ErrStateNotFound
	}
	delete(m.items, state)
	if time.Now().After(sess.ExpiresAt) {
		return nil, ErrStateNotFound
	}
	return &sess, nil
}

// StartJanitor periodically removes expired entries
func (m *Memory) StartJanitor(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = time.Minute
	}
	ticker := time.NewTicker(interval)
	go func() {
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				m.purgeExpired()
			}
		}
	}()
}

func (m *Memory) purgeExpired() {
	now := time.Now()
	m.mu.Lock()
	defer m.mu.Unlock()
	for k, v := range m.items {
		if now.After(v.ExpiresAt) {
			delete(m.items, k)
		}
	}
}
