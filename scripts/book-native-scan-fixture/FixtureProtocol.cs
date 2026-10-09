using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Runtime.Versioning;

[assembly: SupportedOSPlatform("linux")]

namespace NativeScannerFixture;

public sealed record TargetIds(int Library, int Series, int Volume, int Chapter, int File);
public sealed record FilePin(string Path, string Sha256);
public sealed record CellChange(string Table, int Id, string Field, string Before, string After);
public sealed record ScanAllowance(string Table, int Id, string Field, string Before, string? After, bool ScanClock, bool NativeColor = false);
public sealed record ParsedKey(string Name, string NormalizedName, int Format);
public sealed record EpubMemberDelta(string Member, string BeforeSha256, string AfterSha256);
public sealed record EvidenceAck(string Phase, string JobUid, string PodUid, string ReceiptSha256, bool DurablyCopied);
public sealed record LiveNativeProof(DateTimeOffset ObservedAt, string PodUid, string ImageTag, string ImageDigest, string TimeZone, TimeSpan ActualUtcOffset, FilePin[] Modules);
public sealed record FixturePacket(int Schema, bool ExplicitRootFixtureApproval, string Phase,
    string JobUid, string PodUid, string Node, string ImageDigest, string HarnessSha256,
    DateTimeOffset JobStartedAt, DateTimeOffset ExpiresAt, FilePin[] Inputs,
    TargetIds Target, string NativeAssemblySha256, string ExpectedSchemaSha256,
    string CandidateDbPath, string OriginalDbPath, string TargetFilePath,
    CellChange[] CatalogDelta, ScanAllowance[] ScanAllowances, ParsedKey[] RetainedParsedKeys,
    string PrivateSourceProofSha256, string NetworkDenyProofSha256,
    EpubMemberDelta[] EpubMemberDeltas);

public static class FixtureProtocol
{
    public const string NativeImageDigest = "sha256:ca6af7a18d7124d014702983c2364e485294f808c1552e9555f2595b7cda7982";
    public const string NativeTag = "0.9.0.2";
    public const string NativeTimeZone = "America/New_York";
    public const string SourceCommit = "6bcd5689385d0e96824982d843c54f15ce784ddc";
    public const string NativeAssembly = "/kavita/Kavita.Server.dll";
    public static readonly string[] NativeModules = [NativeAssembly, "/kavita/Kavita.API.dll", "/kavita/Kavita.Models.dll", "/kavita/Kavita.Services.dll", "/kavita/Kavita.Database.dll", "/kavita/Microsoft.Data.Sqlite.dll", "/kavita/Microsoft.EntityFrameworkCore.dll", "/kavita/Microsoft.EntityFrameworkCore.Relational.dll"];
    public static readonly HashSet<string> CatalogTables = ["Series", "Volume", "Chapter", "MangaFile", "SeriesMetadata", "ExternalSeriesMetadata"];
    public static readonly TargetIds ReviewedTarget = new(1, 1650, 1800, 3358, 3570);
    public static readonly Dictionary<string, string[]> ScanFields = new(StringComparer.Ordinal)
    {
        ["Series"] = ["SortName", "LastFolderScanned", "LastFolderScannedUtc", "LastModified", "LastModifiedUtc"],
        ["Chapter"] = ["Count", "IsSpecial", "Range", "Title", "TotalCount", "LastModified", "LastModifiedUtc"],
        ["MangaFile"] = ["Bytes", "KoreaderHash", "LastModified", "LastModifiedUtc", "LastFileAnalysis", "LastFileAnalysisUtc"],
        ["SeriesMetadata"] = ["TotalCount", "PublicationStatus", "RowVersion"],
        ["Volume"] = ["PrimaryColor", "SecondaryColor", "LastModified", "LastModifiedUtc"]
    };
    public static bool IsScanClock(string field) => field is "LastModified" or "LastModifiedUtc" or "LastFolderScanned" or "LastFolderScannedUtc" or "LastFileAnalysis" or "LastFileAnalysisUtc";
    public static bool IsNativeColor(string table, string field) => table == "Volume" && field is "PrimaryColor" or "SecondaryColor";
    public static void RequireNativeColor(string cell) => Require(cell == "null:" || (cell.StartsWith("text:#", StringComparison.Ordinal)
        && cell.Length == 12 && cell[6..].All(c => c is >= '0' and <= '9' or >= 'A' and <= 'F')), "native color grammar differs");
    public static readonly Dictionary<string, string> FixedScanValues = new(StringComparer.Ordinal)
    {
        ["Series:SortName"] = "text:Ransom", ["Chapter:Count"] = "int:0", ["Chapter:IsSpecial"] = "int:1",
        ["Chapter:Range"] = "text:Ransom", ["Chapter:Title"] = "text:Ransom", ["Chapter:TotalCount"] = "int:1",
        ["MangaFile:Bytes"] = "int:1081344", ["SeriesMetadata:TotalCount"] = "int:1", ["SeriesMetadata:PublicationStatus"] = "int:2"
    };
    public static string Sha(byte[] value) => Convert.ToHexStringLower(SHA256.HashData(value));
    public static string HashText(string value) => Sha(Encoding.UTF8.GetBytes(value));
    public static void Require(bool condition, string reason)
    {
        if (!condition) throw new InvalidOperationException(reason);
    }
    public static bool IsHash(string value) => value.Length == 64 && value.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');
    public static string Cell(object? value) => value switch
    {
        null or DBNull => "null:",
        byte[] bytes => "blob:" + Convert.ToBase64String(bytes),
        string text => "text:" + text,
        double number => "real:" + number.ToString("R", CultureInfo.InvariantCulture),
        float number => "real:" + number.ToString("R", CultureInfo.InvariantCulture),
        long or int or short or byte => "int:" + Convert.ToString(value, CultureInfo.InvariantCulture),
        _ => throw new InvalidOperationException("unknown native storage cell type")
    };
    public static string CanonicalRow(SortedDictionary<string, string> row) => JsonSerializer.Serialize(row);
    public static string[] Keys(IEnumerable<CellChange> changes) => changes.Select(c => $"{c.Table}:{c.Id}:{c.Field}").Order(StringComparer.Ordinal).ToArray();
    public static void Validate(FixturePacket packet, DateTimeOffset now)
    {
        Require(packet.Schema == 1 && packet.ExplicitRootFixtureApproval, "exact root fixture approval missing");
        Require(Guid.TryParseExact(packet.Phase, "D", out _) && Guid.TryParseExact(packet.JobUid, "D", out _) && Guid.TryParseExact(packet.PodUid, "D", out _), "native phase/UID binding missing");
        Require(packet.Node == "talosw01" && packet.ImageDigest.StartsWith("sha256:") && IsHash(packet.ImageDigest[7..]), "unreviewed node/image");
        Require(packet.JobStartedAt <= now && now < packet.ExpiresAt && packet.ExpiresAt <= packet.JobStartedAt.AddSeconds(180), "original fixture deadline expired or extended");
        Require(IsHash(packet.HarnessSha256) && IsHash(packet.NativeAssemblySha256) && IsHash(packet.ExpectedSchemaSha256) && IsHash(packet.PrivateSourceProofSha256) && IsHash(packet.NetworkDenyProofSha256), "source/runtime/private-proof pins missing");
        Require(packet.Target == ReviewedTarget, "target is outside the exact reviewed five rows");
        Require(packet.CandidateDbPath == "/kavita/config/kavita.db" && packet.OriginalDbPath == "/fixture-input/original.db", "database path is outside the isolated fixture");
        Require(packet.TargetFilePath.StartsWith("/data/cephfs-hdd/data/media/books/EBooks/", StringComparison.Ordinal) && packet.TargetFilePath.EndsWith(".epub", StringComparison.Ordinal) && !packet.TargetFilePath.Contains(".."), "isolated EPUB path missing");
        var expected = new[] { $"Series:{packet.Target.Series}:Name", $"Series:{packet.Target.Series}:NormalizedName", $"Series:{packet.Target.Series}:OriginalName", $"Volume:{packet.Target.Volume}:LookupName", $"Volume:{packet.Target.Volume}:Name", $"Volume:{packet.Target.Volume}:MinNumber", $"Volume:{packet.Target.Volume}:MaxNumber" }.Order(StringComparer.Ordinal).ToArray();
        Require(Keys(packet.CatalogDelta).SequenceEqual(expected), "catalog delta exceeded the seven reviewed fields");
        var scanKeys = ScanFields.SelectMany(pair => pair.Value.Select(field => $"{pair.Key}:{TargetRowId(packet, pair.Key)}:{field}")).Order(StringComparer.Ordinal);
        Require(packet.ScanAllowances.Select(r => $"{r.Table}:{r.Id}:{r.Field}").Order(StringComparer.Ordinal).SequenceEqual(scanKeys), "scan allowance differs from exact 25 reviewed cells");
        foreach (var rule in packet.ScanAllowances)
        {
            Require(CatalogTables.Contains(rule.Table), "saved-state/curation table cannot be allowed");
            Require(rule.Id == TargetRowId(packet, rule.Table), "scan allowance includes another work");
            Require(!rule.Field.EndsWith("Locked", StringComparison.Ordinal) && !rule.Field.Contains("Override", StringComparison.OrdinalIgnoreCase), "saved lock/override cannot be allowed");
            Require(!packet.CatalogDelta.Any(c => c.Table == rule.Table && c.Id == rule.Id && c.Field == rule.Field), "scan cannot change the reviewed work key");
            Require(rule.NativeColor == IsNativeColor(rule.Table, rule.Field) && !(rule.NativeColor && rule.ScanClock)
                && (rule.ScanClock || rule.NativeColor) == (rule.After is null), "each allowance must use its exact scalar, clock or native-color policy");
            Require(rule.ScanClock == IsScanClock(rule.Field), "scan-clock policy differs");
            if (FixedScanValues.TryGetValue(rule.Table + ":" + rule.Field, out var fixedValue)) Require(rule.After == fixedValue, "fixed native scalar differs");
            if (rule.Table == "SeriesMetadata" && rule.Field == "RowVersion") Require(rule.After == NativeProjection.NextRowVersion(rule.Before), "exact native uint token differs");
        }
        Require(packet.ScanAllowances.Select(r => $"{r.Table}:{r.Id}:{r.Field}").Distinct().Count() == packet.ScanAllowances.Length, "duplicate scan allowance");
        Require(packet.ScanAllowances.Count(r => r.Table == "Series" && r.Id == packet.Target.Series && r.Field == "LastFolderScanned" && r.ScanClock) == 1, "actual target scan-clock proof missing");
        Require(packet.Inputs.Length is >= 7 and <= 8 && packet.Inputs.All(p => IsHash(p.Sha256)), "complete bounded input pins missing");
        Require(packet.Inputs.Select(p => p.Path).Distinct().Count() == packet.Inputs.Length, "duplicate input pin");
        foreach (var required in new[] { packet.CandidateDbPath, packet.OriginalDbPath, packet.TargetFilePath, "/fixture-input/original.epub", "/kavita/config/appsettings.json", "/fixture-input/source-proof.json", "/fixture-input/network-deny-proof.json" })
            Require(packet.Inputs.Count(p => p.Path == required) == 1, "native input is not exactly hash-bound");
        Require(packet.Inputs.Single(p => p.Path == "/fixture-input/source-proof.json").Sha256 == packet.PrivateSourceProofSha256 && packet.Inputs.Single(p => p.Path == "/fixture-input/network-deny-proof.json").Sha256 == packet.NetworkDenyProofSha256, "private evidence pin differs");
        Require(packet.RetainedParsedKeys.Length is > 0 and <= 5000 && packet.RetainedParsedKeys.All(k => k.Name.Length > 0 && k.NormalizedName.Length > 0 && k.Format > 0), "complete retained parsed keys missing");
        Require(packet.RetainedParsedKeys.Select(k => $"{k.Format}:{k.NormalizedName}").Distinct().Count() == packet.RetainedParsedKeys.Length, "ambiguous retained parsed keys");
        Require(packet.EpubMemberDeltas.Length is > 0 and <= 4 && packet.EpubMemberDeltas.All(d => d.Member.EndsWith(".opf", StringComparison.OrdinalIgnoreCase) && !d.Member.Contains("..") && IsHash(d.BeforeSha256) && IsHash(d.AfterSha256) && d.BeforeSha256 != d.AfterSha256), "reviewed series-only OPF member pins missing");
        Require(packet.EpubMemberDeltas.Select(d => d.Member).Distinct().Count() == packet.EpubMemberDeltas.Length, "duplicate OPF member allowance");
    }
    public static int TargetRowId(FixturePacket p, string table) => table switch
    {
        "Series" or "SeriesMetadata" or "ExternalSeriesMetadata" => p.Target.Series,
        "Volume" => p.Target.Volume, "Chapter" => p.Target.Chapter, "MangaFile" => p.Target.File,
        _ => throw new InvalidOperationException("unapproved native table")
    };
    public static void ValidateAck(FixturePacket p, EvidenceAck ack, string receiptSha256) => Require(
        ack.Phase == p.Phase && ack.JobUid == p.JobUid && ack.PodUid == p.PodUid && ack.ReceiptSha256 == receiptSha256 && ack.DurablyCopied,
        "private artifact durable ACK differs");
    public static void ValidateLiveNative(FixturePacket p, LiveNativeProof proof, DateTimeOffset now)
    {
        Require(proof.ObservedAt >= p.JobStartedAt && proof.ObservedAt <= now && now < p.ExpiresAt, "fresh live native proof outside original Job phase");
        Require(Guid.TryParseExact(proof.PodUid, "D", out _) && proof.ImageTag == NativeTag && proof.ImageDigest == NativeImageDigest && proof.TimeZone == NativeTimeZone, "production native identity changed; reviewed successor required");
        Require(proof.ActualUtcOffset == TimeZoneInfo.FindSystemTimeZoneById(NativeTimeZone).GetUtcOffset(proof.ObservedAt), "live native timezone offset differs");
        Require(proof.Modules.Select(m => m.Path).Order(StringComparer.Ordinal).SequenceEqual(NativeModules.Order(StringComparer.Ordinal)) && proof.Modules.All(m => IsHash(m.Sha256)), "complete current native module closure missing");
        Require(proof.Modules.Single(m => m.Path == NativeAssembly).Sha256 == p.NativeAssemblySha256, "live native assembly changed");
    }
    public static void RequireScanClock(string field, string actual, DateTimeOffset started, DateTimeOffset finished)
    {
        var styles = field.EndsWith("Utc", StringComparison.Ordinal)
            ? DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal
            : DateTimeStyles.AssumeLocal;
        Require(actual.StartsWith("text:", StringComparison.Ordinal)
            && DateTimeOffset.TryParse(actual[5..], CultureInfo.InvariantCulture, styles, out var time)
            && time >= started.AddSeconds(-1) && time <= finished.AddSeconds(1), "native scan clock outside invocation");
    }
    public static void ScanClockSelfTest()
    {
        Require(TimeZoneInfo.Local.Id == NativeTimeZone, "clock fixture local timezone differs");
        var at = DateTimeOffset.Parse("2026-10-09T19:00:00Z", CultureInfo.InvariantCulture);
        foreach (var (field, cell) in new[] {
            ("LastFolderScanned", "text:2026-10-09 15:00:00"),
            ("LastFolderScannedUtc", "text:2026-10-09 19:00:00"),
            ("LastModified", "text:2026-10-09T15:00:00-04:00"),
            ("LastModifiedUtc", "text:2026-10-09T19:00:00+00:00") })
            RequireScanClock(field, cell, at, at);
        foreach (var (field, cell) in new[] {
            ("LastFolderScannedUtc", "text:2026-10-09 15:00:00"),
            ("LastFolderScanned", "text:2026-10-09 19:00:00"),
            ("LastModifiedUtc", "text:2026-10-09 18:59:58"),
            ("LastModifiedUtc", "text:2026-10-09 19:00:02"),
            ("LastModifiedUtc", "text:not-a-timestamp") })
        {
            var refused = false;
            try { RequireScanClock(field, cell, at, at); }
            catch (InvalidOperationException) { refused = true; }
            Require(refused, "invalid native UTC/local scan clock accepted");
        }
    }
    public static void SelfTest()
    {
        ScanClockSelfTest();
        Require(Cell("1") != Cell(1L) && Cell(1L) != Cell(1.0), "typed storage boundaries lost");
        Require(Cell(new byte[] { 1, 2 }) == "blob:AQI=", "binary storage changed");
        Require(!CatalogTables.Contains("AppUserProgresses") && !CatalogTables.Contains("AppUserReadingHistory") && !CatalogTables.Contains("ReadingListItem"), "saved-state table allowance");
        Require(SourceCommit.Length == 40 && IsHash(NativeImageDigest[7..]), "native source pins missing");
        var now = DateTimeOffset.Parse("2026-10-09T00:00:00Z", CultureInfo.InvariantCulture);
        var hash = new string('a', 64);
        var target = ReviewedTarget;
        var delta = new CellChange[] { new("Series", target.Series, "Name", "text:Before", "text:Ransom"), new("Series", target.Series, "NormalizedName", "text:before", "text:ransom"), new("Series", target.Series, "OriginalName", "text:Before", "text:Ransom"), new("Volume", target.Volume, "LookupName", "text:1", "text:-100000"), new("Volume", target.Volume, "Name", "text:1", "text:-100000"), new("Volume", target.Volume, "MinNumber", "real:1", "real:-100000"), new("Volume", target.Volume, "MaxNumber", "real:1", "real:-100000") };
        var paths = new[] { "/kavita/config/kavita.db", "/fixture-input/original.db", "/data/cephfs-hdd/data/media/books/EBooks/Fixture/Work.epub", "/kavita/config/appsettings.json", "/fixture-input/source-proof.json", "/fixture-input/network-deny-proof.json", "/fixture-input/original.epub" };
        var good = new FixturePacket(1, true, Guid.NewGuid().ToString(), Guid.NewGuid().ToString(), Guid.NewGuid().ToString(), "talosw01", NativeImageDigest, hash, now, now.AddSeconds(180), paths.Select(p => new FilePin(p, hash)).ToArray(), target, hash, hash, paths[0], paths[1], paths[2], delta, [], [new("Ransom", "ransom", 3)], hash, hash, [new("fixture.opf", hash, new string('b', 64))]);
        good = good with { ScanAllowances = ScanFields.SelectMany(pair => pair.Value.Select(field => new ScanAllowance(pair.Key, TargetRowId(good, pair.Key), field,
            field == "RowVersion" ? "int:100" : IsScanClock(field) ? "text:2000-01-01 00:00:00" : IsNativeColor(pair.Key, field) ? "text:#AABBCC" : field is "Count" or "TotalCount" or "IsSpecial" or "Bytes" or "PublicationStatus" ? "int:4" : "text:before",
            IsScanClock(field) || IsNativeColor(pair.Key, field) ? null : FixedScanValues.TryGetValue(pair.Key + ":" + field, out var value) ? value : field == "RowVersion" ? "int:102" : "text:" + new string('a', 32), IsScanClock(field), IsNativeColor(pair.Key, field)))).ToArray() };
        SyntheticPacket = good;
        Validate(good, now);
        var invalid = new[] {
            good with { ExplicitRootFixtureApproval = false }, good with { ExpiresAt = now.AddSeconds(181) }, good with { ExpiresAt = now }, good with { PodUid = "unknown" },
            good with { CandidateDbPath = "/production/live.db" }, good with { CatalogDelta = delta[..6] },
            good with { ScanAllowances = [new("AppUserProgresses", target.Series, "BookScrollId", "text:before", "text:after", false)] },
            good with { ScanAllowances = [good.ScanAllowances[0], new("Series", target.Series, "NameLocked", "int:0", "int:1", false)] },
            good with { ScanAllowances = [good.ScanAllowances[0], new("Series", 99, "Name", "text:before", "text:after", false)] },
            good with { Inputs = good.Inputs[..5] }, good with { RetainedParsedKeys = [good.RetainedParsedKeys[0], good.RetainedParsedKeys[0]] },
            good with { ScanAllowances = good.ScanAllowances.Append(new("Volume", target.Volume, "CoverImage", "text:old", "text:new", false)).ToArray() },
            good with { ScanAllowances = good.ScanAllowances.Select(r => r.NativeColor ? r with { After = "text:#112233" } : r).ToArray() }
        };
        foreach (var candidate in invalid)
        {
            var refused = false;
            try { Validate(candidate, now); } catch (InvalidOperationException) { refused = true; }
            Require(refused, "finite malformed fixture packet accepted");
        }
        var ack = new EvidenceAck(good.Phase, good.JobUid, good.PodUid, hash, true);
        ValidateAck(good, ack, hash);
        foreach (var bad in new[] { ack with { PodUid = Guid.NewGuid().ToString() }, ack with { ReceiptSha256 = new string('b', 64) }, ack with { DurablyCopied = false } })
        {
            var refused = false;
            try { ValidateAck(good, bad, hash); } catch (InvalidOperationException) { refused = true; }
            Require(refused, "invalid durable evidence ACK accepted");
        }
        var live = new LiveNativeProof(now, Guid.NewGuid().ToString(), NativeTag, NativeImageDigest, NativeTimeZone, TimeZoneInfo.FindSystemTimeZoneById(NativeTimeZone).GetUtcOffset(now), NativeModules.Select(path => new FilePin(path, hash)).ToArray());
        ValidateLiveNative(good, live, now);
        foreach (var bad in new[] { live with { ImageDigest = "sha256:" + new string('b', 64) }, live with { ImageTag = "different" }, live with { TimeZone = "UTC" }, live with { ActualUtcOffset = TimeSpan.Zero }, live with { ObservedAt = now.AddSeconds(-1) }, live with { Modules = live.Modules[..7] } })
        {
            var refused = false;
            try { ValidateLiveNative(good, bad, now); } catch (InvalidOperationException) { refused = true; }
            Require(refused, "stale or different live native proof accepted");
        }
    }
    internal static FixturePacket? SyntheticPacket { get; private set; }
}
