for ip in 10.42.0.152 10.42.1.100 10.42.2.80; do
  echo "=== $ip ==="
  for i in {1..5}; do
    nslookup api.allegion.yonomi.cloud $ip || echo "FAIL"
  done
done
