for i in $(seq 1 50); do
  printf "%02d: " $i
  dig +time=1 +tries=1 api.allegion.yonomi.cloud A | grep "Query time" || echo "timeout"
  sleep 1
done
