using System.Text.Json;
// Generic ASCII-only vectors, never private native schema or data.
string[] values = ["simple", "quote\"and'single", "<&+>`", "\\\t\n\r\b\f", "\0\v\x7f", "/plain path", "text:CREATE TABLE \"Synthetic\" (\"Value\" TEXT DEFAULT 'x');"];
Console.WriteLine(JsonSerializer.Serialize(values));
Console.WriteLine(JsonSerializer.Serialize(new[] { JsonSerializer.Serialize(values), JsonSerializer.Serialize(new[] { "text:index", "null:" }) }));
