//! In-memory token-bucket rate limiting for the public API.
//!
//! One bucket per API token (60 requests/minute, burst 20) and one per client IP for requests
//! that carry no valid token (failed auth, public endpoints) so a guessing client is throttled
//! long before it can enumerate anything. Buckets live in process memory: a restart forgives
//! everyone, which is fine for a courtesy limit that is not a billing meter.

use std::{
    collections::HashMap,
    sync::Mutex,
    time::{Duration, Instant},
};

/// Refill rate and burst for a token bucket.
#[derive(Clone, Copy)]
pub(crate) struct Rate {
    pub(crate) per_minute: u32,
    pub(crate) burst: u32,
}

pub(crate) const TOKEN_RATE: Rate = Rate {
    per_minute: 60,
    burst: 20,
};
pub(crate) const IP_RATE: Rate = Rate {
    per_minute: 30,
    burst: 20,
};

/// Soft cap on tracked keys; past it, idle full buckets are dropped.
const MAX_KEYS: usize = 20_000;

struct Bucket {
    tokens: f64,
    last: Instant,
}

/// What a bucket said about one request, shaped for the `X-RateLimit-*` headers.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Verdict {
    pub(crate) allowed: bool,
    pub(crate) limit: u32,
    pub(crate) remaining: u32,
    /// Seconds until the bucket is full again.
    pub(crate) reset_secs: u64,
    /// Seconds until the next request would pass (only meaningful when `!allowed`).
    pub(crate) retry_after_secs: u64,
}

#[derive(Default)]
pub(crate) struct Limiter {
    buckets: Mutex<HashMap<String, Bucket>>,
}

impl Limiter {
    /// Takes one request's worth from `key`'s bucket.
    pub(crate) fn check(&self, key: &str, rate: Rate) -> Verdict {
        self.check_at(key, rate, Instant::now())
    }

    fn check_at(&self, key: &str, rate: Rate, now: Instant) -> Verdict {
        let per_sec = f64::from(rate.per_minute) / 60.0;
        let burst = f64::from(rate.burst);
        let mut map = self.buckets.lock().unwrap_or_else(|e| e.into_inner());

        if map.len() >= MAX_KEYS {
            map.retain(|_, b| b.tokens + now.duration_since(b.last).as_secs_f64() * per_sec < burst);
        }

        let bucket = map.entry(key.to_string()).or_insert(Bucket {
            tokens: burst,
            last: now,
        });
        let elapsed = now.saturating_duration_since(bucket.last).as_secs_f64();
        bucket.tokens = (bucket.tokens + elapsed * per_sec).min(burst);
        bucket.last = now;

        let allowed = bucket.tokens >= 1.0;
        if allowed {
            bucket.tokens -= 1.0;
        }
        let secs = |t: f64| Duration::from_secs_f64(t.max(0.0)).as_secs_f64().ceil() as u64;
        Verdict {
            allowed,
            limit: rate.per_minute,
            remaining: bucket.tokens.floor() as u32,
            reset_secs: secs((burst - bucket.tokens) / per_sec),
            retry_after_secs: if allowed {
                0
            } else {
                secs((1.0 - bucket.tokens) / per_sec).max(1)
            },
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn burst_then_throttle_then_refill() {
        let limiter = Limiter::default();
        let t0 = Instant::now();
        for i in 0..20 {
            let v = limiter.check_at("t", TOKEN_RATE, t0);
            assert!(v.allowed, "request {i} within the burst");
            assert_eq!(v.remaining, 19 - i);
        }
        let denied = limiter.check_at("t", TOKEN_RATE, t0);
        assert!(!denied.allowed);
        assert_eq!(denied.retry_after_secs, 1);

        // One request per second refills one token.
        assert!(limiter.check_at("t", TOKEN_RATE, t0 + Duration::from_secs(1)).allowed);
        assert!(!limiter.check_at("t", TOKEN_RATE, t0 + Duration::from_secs(1)).allowed);
    }

    #[test]
    fn keys_are_independent() {
        let limiter = Limiter::default();
        let t0 = Instant::now();
        for _ in 0..20 {
            limiter.check_at("a", TOKEN_RATE, t0);
        }
        assert!(!limiter.check_at("a", TOKEN_RATE, t0).allowed);
        assert!(limiter.check_at("b", TOKEN_RATE, t0).allowed);
    }
}
