// In-process C ABI for the OP441 model child. Build against the headers
// and shared libraries of llama.cpp af5c58347fdbc2cd19e65b278e81c8f528f8a175.
#include "chat.h"
#include "ggml-backend.h"
#include "llama.h"
#include "log.h"
#include "mtmd-helper.h"
#include "sampling.h"

#define STBI_ONLY_PNG
#define STBI_NO_STDIO
#define STBI_MAX_DIMENSIONS 4096
#define STB_IMAGE_STATIC
#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"

#include <algorithm>
#include <cctype>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace {
constexpr int threads = 2;
constexpr int batch_size = 1024;
constexpr size_t max_frames = 4;

template<class T, void (*Free)(T *)>
using Owned = std::unique_ptr<T, decltype(Free)>;

struct Model {
    Owned<llama_model, llama_model_free> weights{nullptr, llama_model_free};
    Owned<llama_context, llama_free> context{nullptr, llama_free};
    Owned<mtmd_context, mtmd_free> vision{nullptr, mtmd_free};
    common_chat_templates_ptr templates;
    const llama_vocab * vocab = nullptr;
    uint32_t context_tokens = 0;
};

struct Prepared {
    common_chat_session chat;
    Owned<mtmd_input_chunks, mtmd_input_chunks_free> chunks{mtmd_input_chunks_init(), mtmd_input_chunks_free};
    bool structured = false;
    bool expects_tool = false;
    std::string answer;
};

void discard_request_log(ggml_log_level, const char *, void *) {}

bool blank(const std::string & text) {
    return std::all_of(text.begin(), text.end(), [](unsigned char c) { return std::isspace(c); });
}

common_chat_templates_inputs chat_inputs(const char * prompt, size_t frame_count,
                                         const char * schema, const char * tool) {
    common_chat_templates_inputs inputs;
    inputs.enable_thinking = false;
    // Qwen's disabled-thinking prefix includes an empty think block.
    // Extract it separately so it cannot be mistaken for tool-call content.
    inputs.reasoning_format = COMMON_REASONING_FORMAT_AUTO;
    inputs.parallel_tool_calls = false;
    inputs.tool_choice = COMMON_CHAT_TOOL_CHOICE_NONE;
    if (*schema) {
        inputs.json_schema = schema;
        common_chat_msg system;
        system.role = "system";
        system.content = "Return only JSON matching this schema. Preserve input image order if present.\n";
        system.content += schema;
        inputs.messages.push_back(std::move(system));
    }
    if (*tool) {
        auto definition = common_json::parse(tool);
        inputs.tools.push_back({definition.at("name").get<std::string>(),
                                definition.at("description").get<std::string>(),
                                definition.at("parameters").dump()});
        inputs.tool_choice = COMMON_CHAT_TOOL_CHOICE_REQUIRED;
    }
    common_chat_msg user;
    user.role = "user";
    for (size_t i = 0; i < frame_count; ++i) {
        user.content_parts.push_back({"media_marker", mtmd_default_marker(), ""});
    }
    user.content_parts.push_back({"text", prompt, ""});
    inputs.messages.push_back(std::move(user));
    return inputs;
}

std::string structured_answer(common_chat_session & session, const common_chat_input & output,
                              bool expects_tool) {
    try {
        const auto & message = session.finish(output);
        if (!blank(message.reasoning_content)) {
            return "{}";
        }
        if (!expects_tool) {
            return message.tool_calls.empty() ? message.content : "{}";
        }
        if (!blank(message.content) || message.tool_calls.size() != 1) {
            return "{}";
        }
        const auto & call = message.tool_calls.front();
        if (call.name != "record_plan_v1") {
            return "{}";
        }
        // Preserve the complete arguments for independent schema validation.
        return "{\"name\":\"record_plan_v1\",\"arguments\":" + call.arguments + "}";
    } catch (const std::runtime_error &) {
        return "{}";
    }
}

void tokenize(Model & model, Prepared & request,
              const std::vector<Owned<mtmd_bitmap, mtmd_bitmap_free>> & images) {
    const std::string marker = mtmd_default_marker();
    const auto & parts = request.chat.prompt().parts;
    std::vector<mtmd_input_text> text;
    std::vector<mtmd_input_part> input;
    // Each marker can split at most one text part and add one bitmap part.
    text.reserve(parts.size() + images.size());
    input.reserve(parts.size() + 2 * images.size());
    size_t image_index = 0;
    for (const auto & part : parts) {
        size_t start = 0;
        while (start < part.text.size()) {
            const size_t found = part.text.find(marker, start);
            const size_t end = std::min(found, part.text.size());
            if (end > start) {
                text.push_back({part.text.data() + start, end - start, false, !part.is_input});
                input.push_back({&text.back(), nullptr});
            }
            if (found == std::string::npos) {
                break;
            }
            input.push_back({nullptr, images.at(image_index++).get()});
            start = found + marker.size();
        }
    }
    std::vector<const mtmd_input_part *> pointers;
    for (const auto & part : input) {
        pointers.push_back(&part);
    }
    if (image_index != images.size() || mtmd_tokenize_from_parts(
            model.vision.get(), request.chunks.get(), pointers.data(), pointers.size(), true)) {
        throw std::runtime_error("Prompt tokenization failed");
    }
}

void decode_token(llama_context * ctx, llama_batch_ext * batch, llama_token token, llama_pos pos) {
    llama_batch_ext_clear(batch);
    const auto index = llama_batch_ext_add_token(batch, 0, token);
    if (index < 0 || !llama_batch_ext_set_pos(batch, index, &pos) ||
        !llama_batch_ext_set_output_logits(batch, index, true) ||
        llama_process(ctx, LLAMA_PROCESS_TYPE_DECODE, batch) != 0) {
        throw std::runtime_error("Token decode failed");
    }
}
} // namespace

extern "C" Model * studio_model_open(const char * weights, const char * projector, uint32_t context,
                                     int32_t max_image_tokens) noexcept {
    try {
        // The child is the only process that initializes CUDA. The container
        // has exactly one visible CUDA device, selected by UUID before fork.
        ggml_backend_load_all_from_path("/app");
        auto cuda = ggml_backend_reg_by_name("CUDA");
        if (!cuda || ggml_backend_reg_dev_count(cuda) != 1) {
            return nullptr;
        }
        ggml_backend_dev_t devices[] = {ggml_backend_reg_dev_get(cuda, 0), nullptr};
        llama_backend_init();
        common_log_set_verbosity_thold(-1); // never log prompt or parser output
        auto model = std::make_unique<Model>();

        auto params = llama_model_default_params();
        params.devices = devices;
        params.n_gpu_layers = 999;
        params.split_mode = LLAMA_SPLIT_MODE_NONE;
        model->weights.reset(llama_model_load_from_file(weights, params));
        if (!model->weights || !llama_model_chat_template(model->weights.get(), nullptr)) {
            return nullptr;
        }
        model->templates = common_chat_templates_init(model->weights.get(), "");
        model->vocab = llama_model_get_vocab(model->weights.get());

        auto ctx = llama_context_default_params();
        ctx.n_ctx = context;
        ctx.n_seq_max = 1;
        ctx.n_batch = batch_size;
        ctx.n_ubatch = batch_size;
        ctx.n_threads = threads;
        ctx.n_threads_batch = threads;
        ctx.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
        ctx.type_k = GGML_TYPE_F16;
        ctx.type_v = GGML_TYPE_F16;
        model->context.reset(llama_init_from_model(model->weights.get(), ctx));
        if (!model->context || llama_n_ctx_seq(model->context.get()) < context) {
            return nullptr;
        }
        model->context_tokens = context;

        auto vision = mtmd_context_params_default();
        vision.use_gpu = true;
        vision.device = devices[0];
        vision.n_threads = threads;
        vision.flash_attn_type = ctx.flash_attn_type;
        vision.warmup = false;
        vision.image_max_tokens = max_image_tokens;
        vision.batch_max_tokens = max_image_tokens;
        model->vision.reset(mtmd_init_from_file(projector, model->weights.get(), vision));
        if (!model->vision || !mtmd_support_vision(model->vision.get())) {
            return nullptr;
        }
        llama_synchronize(model->context.get());
        // The pinned tokenizers can log raw input even with common's
        // verbosity disabled. Keep public load diagnostics only.
        mtmd_helper_log_set(discard_request_log, nullptr);
        llama_log_set(discard_request_log, nullptr);
        ggml_log_set(discard_request_log, nullptr);
        return model.release();
    } catch (...) {
        return nullptr;
    }
}

extern "C" void studio_model_close(Model * model) noexcept {
    llama_synchronize(model->context.get());
    delete model;
    llama_backend_free();
    // Driver state is process-scoped. The supervisor reaps this child and
    // measures fresh memory before treating the GPU as released.
}

extern "C" Prepared * studio_prepare(Model * model, const char * prompt,
                                     const unsigned char * const * pngs, const int * sizes, size_t count,
                                     const char * schema, const char * tool,
                                     size_t * total, size_t * visual) noexcept {
    try {
        if (count > max_frames || std::string_view(prompt).find(mtmd_default_marker()) != std::string_view::npos) {
            return nullptr;
        }
        std::vector<Owned<mtmd_bitmap, mtmd_bitmap_free>> images;
        for (size_t i = 0; i < count; ++i) {
            int width, height, channels;
            std::unique_ptr<unsigned char, decltype(&stbi_image_free)> pixels(
                stbi_load_from_memory(pngs[i], sizes[i], &width, &height, &channels, 3), stbi_image_free);
            if (!pixels) {
                return nullptr;
            }
            images.emplace_back(mtmd_bitmap_init(width, height, pixels.get()), mtmd_bitmap_free);
            if (!images.back()) {
                return nullptr;
            }
        }

        auto request = std::make_unique<Prepared>();
        auto inputs = chat_inputs(prompt, count, schema, tool);
        request->chat = common_chat_session(model->templates.get(), model->vocab, inputs);
        request->expects_tool = *tool != '\0';
        request->structured = *schema != '\0' || request->expects_tool;
        if (request->structured && request->chat.grammar().empty()) {
            return nullptr;
        }
        tokenize(*model, *request, images);
        size_t image_index = 0;
        for (size_t i = 0; i < mtmd_input_chunks_size(request->chunks.get()); ++i) {
            const auto * chunk = mtmd_input_chunks_get(request->chunks.get(), i);
            if (mtmd_input_chunk_get_type(chunk) == MTMD_INPUT_CHUNK_TYPE_IMAGE) {
                if (image_index == count) {
                    return nullptr;
                }
                visual[image_index++] = mtmd_input_chunk_get_n_tokens(chunk);
            }
        }
        if (image_index != count) {
            return nullptr;
        }
        // KV capacity is in tokens. Also guard positions for M-RoPE.
        const auto tokens = mtmd_helper_get_n_tokens(request->chunks.get());
        const auto positions = mtmd_helper_get_n_pos(request->chunks.get());
        *total = std::max(tokens, static_cast<size_t>(positions));
        return request.release();
    } catch (...) {
        return nullptr;
    }
}

extern "C" void studio_request_free(Prepared * request) noexcept {
    delete request;
}

extern "C" const char * studio_generate(Model * model, Prepared * request, int limit, size_t byte_limit,
                                        int * output_tokens, bool * complete, size_t * size) noexcept {
    try {
        auto * ctx = model->context.get();
        auto * memory = llama_get_memory(ctx);
        const auto tokens = mtmd_helper_get_n_tokens(request->chunks.get());
        const auto positions = mtmd_helper_get_n_pos(request->chunks.get());
        if (!tokens || limit < 1 || std::max(tokens, static_cast<size_t>(positions)) + limit > model->context_tokens) {
            return nullptr;
        }

        // A single request owns the context. Never retain prompt cache across
        // requests, including a failed decode or a truncated generation.
        struct ResetMemory {
            llama_context * ctx;
            llama_memory_t memory;
            ~ResetMemory() {
                llama_synchronize(ctx);
                llama_memory_clear(memory, true);
                llama_synchronize(ctx);
            }
        } reset{ctx, memory};
        llama_pos position = 0;
        if (mtmd_helper_eval_chunks(model->vision.get(), ctx, request->chunks.get(),
                                    0, 0, batch_size, true, &position)) {
            return nullptr;
        }
        common_params_sampling sampling;
        sampling.temp = 0;
        request->chat.apply_sampling(sampling);
        Owned<common_sampler, common_sampler_free> sampler(common_sampler_init(model->weights.get(), sampling),
                                                          common_sampler_free);
        Owned<llama_batch_ext, llama_batch_ext_free> batch(llama_batch_ext_init(ctx), llama_batch_ext_free);
        if (!sampler || !batch) {
            return nullptr;
        }
        common_chat_input output;
        for (*output_tokens = 0; *output_tokens < limit;) {
            const auto token = common_sampler_sample(sampler.get(), ctx, -1, true);
            if (token == LLAMA_TOKEN_NULL) {
                return nullptr;
            }
            ++*output_tokens;
            if (llama_vocab_is_eog(model->vocab, token)) {
                *complete = true;
                break;
            }
            common_sampler_accept(sampler.get(), token, true);
            output.append(common_token_to_piece(model->vocab, token, true), token);
            if (output.size() > byte_limit) {
                break;
            }
            for (const auto & stop : request->chat.additional_stops()) {
                const size_t at = output.text.find(stop);
                if (!stop.empty() && at != std::string::npos) {
                    output.truncate(at);
                    *complete = true;
                    break;
                }
            }
            if (*complete || *output_tokens == limit) {
                break;
            }
            decode_token(ctx, batch.get(), token, position++);
        }
        request->answer = request->structured && *complete && output.size() <= byte_limit
            ? structured_answer(request->chat, output, request->expects_tool) : std::move(output.text);
        *size = request->answer.size();
        return request->answer.data();
    } catch (...) {
        return nullptr;
    }
}

extern "C" bool studio_cpu_check(const char * chat_template, const char * plan, const char * tool) noexcept {
    try {
        common_log_set_verbosity_thold(-1);
        auto tmpl = common_chat_templates_init(nullptr, chat_template);
        auto structured = common_chat_session(tmpl.get(), nullptr, chat_inputs("Return the plan.", 0, plan, ""));
        if (structured.grammar().empty() || structured.prompt().str().find("Return the plan.") == std::string::npos) {
            return false;
        }
        const std::string plan_answer = "{\"scene_id\":\"s01\",\"shots\":[{\"technical_direction\":\"Hold.\"}]}";
        if (structured_answer(structured, common_chat_input(plan_answer), false) != plan_answer) {
            return false;
        }
        auto prepare_tool = [&] {
            return common_chat_session(tmpl.get(), nullptr, chat_inputs("Return the plan.", 0, "", tool));
        };
        std::string example =
            "<tool_call>\n<function=record_plan_v1>\n"
            "<parameter=scene_id>\ns01\n</parameter>\n"
            "<parameter=shot_count>\n2\n</parameter>\n"
            "<parameter=technical_direction>\nHold the camera.\n</parameter>\n</function>\n</tool_call>";
        auto called = prepare_tool();
        if (called.grammar().empty() || structured_answer(called, common_chat_input(example), true) == "{}") {
            return false;
        }
        auto invalid = prepare_tool();
        const std::string name = "record_plan_v1";
        example.replace(example.find(name), name.size(), "other_function");
        return structured_answer(invalid, common_chat_input(example), true) == "{}";
    } catch (...) {
        return false;
    }
}
