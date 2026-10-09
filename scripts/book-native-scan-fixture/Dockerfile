ARG SDK_IMAGE
FROM ${SDK_IMAGE} AS build
WORKDIR /build
COPY scripts/book-native-scan-fixture/NativeScannerFixture.csproj ./
COPY scripts/book-native-scan-fixture/FixtureProtocol.cs scripts/book-native-scan-fixture/Program.cs ./
RUN test "$(dotnet --version)" = "10.0.101" \
    && nice -n 19 dotnet publish -c Release -r linux-x64 --self-contained true \
       -p:RuntimeFrameworkVersion=10.0.1 -p:TargetLatestRuntimePatch=false \
       -p:ContinuousIntegrationBuild=true -p:RestoreSources=https://api.nuget.org/v3/index.json \
       -m:1 -o /published \
    && test -x /published/NativeScannerFixture \
    && test -f /published/libhostfxr.so \
    && test -f /published/libcoreclr.so

FROM docker.io/jvmilazz0/kavita@sha256:ca6af7a18d7124d014702983c2364e485294f808c1552e9555f2595b7cda7982
COPY --from=build /published /fixture
ENV LD_LIBRARY_PATH=/kavita DOTNET_EnableDiagnostics=0
WORKDIR /kavita
USER 1000:1000
ENTRYPOINT ["/fixture/NativeScannerFixture"]
