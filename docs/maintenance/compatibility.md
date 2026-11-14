# Proxy-injected SigV4 headers

Granary is an unofficial fork derived from MinIO. Since issue #25, SigV4
requests carrying unsigned `x-amz-*` headers, such as CloudFront's injected
`X-Amz-Cf-Id`, are rejected with HTTP 400. Such deployments must strip the
header at the proxy or use a configuration that does not inject it. This
behavior is not relaxed by an allowlist.
