package hdhive

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// Client calls HDHive OAuth and Open API endpoints
type Client struct {
	baseURL    string
	tokenURL   string
	refreshURL string
	revokeURL  string
	openBase   string
	secret     string
	httpClient *http.Client
}

// NewClient builds an HDHive API client
func NewClient(baseURL, tokenPath, refreshPath, revokePath, openBase, secret string) *Client {
	base := strings.TrimRight(baseURL, "/")
	return &Client{
		baseURL:    base,
		tokenURL:   base + tokenPath,
		refreshURL: base + refreshPath,
		revokeURL:  base + revokePath,
		openBase:   strings.TrimRight(openBase, "/"),
		secret:     secret,
		httpClient: &http.Client{Timeout: 30 * time.Second},
	}
}

// TokenBundle is the OAuth token response data
type TokenBundle struct {
	AccessToken      string   `json:"access_token"`
	RefreshToken     string   `json:"refresh_token"`
	TokenType        string   `json:"token_type"`
	ExpiresIn        int      `json:"expires_in"`
	RefreshExpiresIn int      `json:"refresh_expires_in"`
	Scope            string   `json:"scope"`
	Scopes           []string `json:"scopes"`
}

type apiEnvelope struct {
	Success bool            `json:"success"`
	Code    string          `json:"code"`
	Message string          `json:"message"`
	Data    json.RawMessage `json:"data"`
}

// ExchangeCode exchanges authorization code for tokens
func (c *Client) ExchangeCode(ctx context.Context, code, redirectURI string) (*TokenBundle, error) {
	body := map[string]string{
		"grant_type":   "authorization_code",
		"code":         code,
		"redirect_uri": redirectURI,
	}
	return c.postToken(ctx, c.tokenURL, body)
}

// RefreshToken refreshes user access token
func (c *Client) RefreshToken(ctx context.Context, refreshToken string) (*TokenBundle, error) {
	body := map[string]string{"refresh_token": refreshToken}
	return c.postToken(ctx, c.refreshURL, body)
}

// RevokeToken revokes refresh token
func (c *Client) RevokeToken(ctx context.Context, refreshToken string) error {
	payload, _ := json.Marshal(map[string]string{"refresh_token": refreshToken})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.revokeURL, bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.secret)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return fmt.Errorf("revoke failed: http %d", resp.StatusCode)
	}
	return nil
}

// ProxyOpen forwards a request to HDHive Open API with app secret and user bearer
func (c *Client) ProxyOpen(
	ctx context.Context,
	method, path string,
	query string,
	body []byte,
	bearer string,
) (int, []byte, http.Header, error) {
	path = strings.TrimPrefix(path, "/")
	target := c.openBase + "/" + path
	if query != "" {
		target += "?" + query
	}
	var bodyReader io.Reader
	if len(body) > 0 {
		bodyReader = bytes.NewReader(body)
	}
	req, err := http.NewRequestWithContext(ctx, method, target, bodyReader)
	if err != nil {
		return 0, nil, nil, err
	}
	if len(body) > 0 {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("X-API-Key", c.secret)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return 0, nil, nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, nil, nil, err
	}
	return resp.StatusCode, data, resp.Header.Clone(), nil
}

func (c *Client) postToken(ctx context.Context, url string, body map[string]string) (*TokenBundle, error) {
	payload, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.secret)
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	var env apiEnvelope
	if err := json.Unmarshal(raw, &env); err != nil {
		return nil, fmt.Errorf("invalid token response: %w", err)
	}
	if !env.Success {
		return nil, fmt.Errorf("hdhive error %s: %s", env.Code, env.Message)
	}
	var bundle TokenBundle
	if err := json.Unmarshal(env.Data, &bundle); err != nil {
		return nil, err
	}
	return &bundle, nil
}
