// go-canary: replicate the Go stdlib resolver path used by every affected
// client (flux source-controller, kyverno, alertmanager, authentik outposts).
// dig probes stayed clean for 50k+ queries while Go clients kept failing with
// v6-only dial lists, so the reproduction must use net.DefaultResolver:
// parallel A+AAAA, resolv.conf search expansion, internal retries.
//
// Log lines (promtail -> Loki):
//   ANOMALY kind=no-A          lookup returned addresses but zero IPv4 (the bug)
//   ANOMALY kind=lookup-error  lookup failed outright
//   SLOW                       success but >=2500ms (dropped-packet retries)
//   heartbeat                  per-15m counters, proves liveness
package main

import (
	"context"
	"fmt"
	"net"
	"sync"
	"sync/atomic"
	"time"
)

var hosts = []string{
	"charts.goauthentik.io",                // Cloudflare; frequent flux failer
	"helm.openwebui.com",                   // GitHub Pages/Fastly; frequent flux failer
	"raw.githubusercontent.com",            // Fastly
	"pkg-containers.githubusercontent.com", // Fastly; the host kyverno blocks on
}

const (
	interval      = 5 * time.Second
	heartbeat     = 15 * time.Minute
	slowThreshold = 2500 * time.Millisecond
)

var lookups, anomalies, slows, maxMs atomic.Int64

func ts() string { return time.Now().UTC().Format("2006-01-02T15:04:05Z") }

func probe(h string) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	t0 := time.Now()
	addrs, err := net.DefaultResolver.LookupIPAddr(ctx, h)
	ms := time.Since(t0).Milliseconds()
	lookups.Add(1)
	if ms > maxMs.Load() {
		maxMs.Store(ms)
	}
	if err != nil {
		anomalies.Add(1)
		fmt.Printf("%s ANOMALY kind=lookup-error host=%s dur_ms=%d err=%q\n", ts(), h, ms, err.Error())
		return
	}
	v4, v6 := 0, 0
	for _, a := range addrs {
		if a.IP.To4() != nil {
			v4++
		} else {
			v6++
		}
	}
	if v4 == 0 {
		// dur_ms disambiguates: fast = server answered NODATA for A,
		// ~5000ms+ = A response lost and retries exhausted.
		anomalies.Add(1)
		fmt.Printf("%s ANOMALY kind=no-A host=%s dur_ms=%d v6=%d addrs=%v\n", ts(), h, ms, v6, addrs)
		return
	}
	if ms >= slowThreshold.Milliseconds() {
		slows.Add(1)
		fmt.Printf("%s SLOW host=%s dur_ms=%d v4=%d v6=%d\n", ts(), h, ms, v4, v6)
	}
}

func main() {
	fmt.Printf("%s go-canary start hosts=%v interval=%s\n", ts(), hosts, interval)
	tick := time.NewTicker(interval)
	beat := time.NewTicker(heartbeat)
	for {
		select {
		case <-tick.C:
			var wg sync.WaitGroup
			for _, h := range hosts {
				wg.Add(1)
				go func(h string) { defer wg.Done(); probe(h) }(h)
			}
			wg.Wait()
		case <-beat.C:
			fmt.Printf("%s heartbeat lookups=%d anomalies=%d slow=%d max_ms=%d\n",
				ts(), lookups.Swap(0), anomalies.Swap(0), slows.Swap(0), maxMs.Swap(0))
		}
	}
}
