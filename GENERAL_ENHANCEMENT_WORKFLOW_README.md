# General Enhancement Workflow README

This document describes the client-adjustable parameters for the General Enhancement workflow and how those parameters should be applied to the ComfyUI API JSON workflow.

It is intended for backend developers implementing the workflow on a new website.

## Scope

The General Enhancement workflow is controlled by two types of client inputs:

- Direct node parameters: sliders, text input, image input, and mask input update specific ComfyUI node fields.
- Routing parameters: checkboxes change which nodes are connected together. These are not simple boolean fields inside ComfyUI.

The current backend implementation is in `General_Enhancement_v04.py`.

The current workflow node mapping is based on the ComfyUI API workflow used by `workflow_api_flux_dev_1.19`.

## Client Parameters

| UI Label | API Field | Type | Default | Range / Values | Purpose |
|---|---|---:|---:|---|---|
| Load Image | `image_b64` | string | required | base64 image | Main image uploaded by the user. |
| Mask Image | `mask_b64` | string | generated | base64 image | Mask from the image editor. |
| Has Drawn Mask | `has_drawn_mask` | boolean | `false` | `true`, `false` | Tells the backend whether to use the drawn mask route. |
| Custom Prompt | `custom_prompt` | string | empty | free text | User-provided prompt guidance. |
| Enable General Enhancement | `general_enhance` | boolean | `true` | `true`, `false` | Enables the general enhancement branch. |
| Details | `details` | number | `1.0` | `0.0` to `2.0`, step `0.05` | Controls LoRA detail strength. |
| General Enhance | `general_denoise` | number | `0.1` | `0.0` to `0.45`, step `0.01` | Controls denoise strength for the general enhancement sampler. |
| Advance Details | `advance_details` | boolean | `false` | `true`, `false` | Enables the Flux advanced detail branch. |
| Additional Detail Pass | `additional_detail_pass` | number | `0.35` | `0.0` to `0.7`, step `0.01` | Controls denoise strength for the advanced detail pass. |
| Sharpen | `sharpen` | number | `0.4` | `0.0` to `1.0`, step `0.01` | Controls blend strength for the sharpen/detail result. |
| Enable Body Enhancement | `body_enhance` | boolean | `false` | `true`, `false` | Enables body and face enhancement. |
| Body Enhancement | `body_enhancement_denoise` | number | `0.2` | `0.0` to `0.3`, step `0.01` | Controls body/person enhancement denoise. |
| Face Enhancement | `face_enhancement_denoise` | number | `0.2` | `0.0` to `0.3`, step `0.01` | Controls face enhancement denoise. |
| Workflow Debug | `workflow_debug` | boolean | `false` | `true`, `false` | Admin-only backend option to save the final workflow JSON. |
| Workflow Profile | `workflow` | string | `General_Enhancement_v04` | configured workflow name | Backend workflow/profile selector. |

## Website Visibility Rules

The website should show or hide advanced controls based on the checkbox state.

| Checkbox | Show These Controls When Enabled |
|---|---|
| `general_enhance` | `details`, `general_denoise` |
| `advance_details` | `additional_detail_pass`, `sharpen` |
| `body_enhance` | `body_enhancement_denoise`, `face_enhancement_denoise` |

Even when a slider is hidden, the backend should still send a valid default value to keep the workflow payload complete.

## Direct ComfyUI Node Mapping

The following parameters directly update ComfyUI node inputs.

| Client Parameter | API Field | ComfyUI Node ID | Node Class / Title | JSON Field Updated | Expected Behavior |
|---|---|---:|---|---|---|
| Input Image | `image_b64` | `63` | `ETN_LoadImageBase64` / Load Image Base64 | `inputs.image` | Sets the main uploaded image. |
| Mask Image | `mask_b64` | `86` | `ETN_LoadImageBase64` / Load Image Base64 | `inputs.image` | Sets the mask image from the editor. |
| Custom Prompt | `custom_prompt` | `35` | `StringFunction\|pysssss` | `inputs.text_a` | Adds user prompt text to the prompt chain. |
| Custom Prompt | `custom_prompt` | `33` | `AILab_QwenVL_GGUF` / QwenVL | `inputs.custom_prompt` | Passes user text to the Qwen prompt helper. |
| Details | `details` | `37` | `LoraLoader` | `inputs.strength_model` | Controls model-side LoRA detail strength. |
| General Enhance | `general_denoise` | `32` | `KSampler` | `inputs.denoise` | Controls the general enhancement denoise amount. |
| Additional Detail Pass | `additional_detail_pass` | `23` | `BasicScheduler` | `inputs.denoise` | Controls advanced detail denoise amount. |
| Sharpen | `sharpen` | `74` | `Blend` | `inputs.blend_factor` | Controls blend/sharpen amount. |
| Body Enhancement | `body_enhancement_denoise` | `52` | `FaceDetailerPipe` | `inputs.denoise` | Controls body/person detailer strength. |
| Face Enhancement | `face_enhancement_denoise` | `54` | `FaceDetailerPipe` | `inputs.denoise` | Controls face detailer strength. |

## Backend-Generated Node Values

The backend generates fresh seeds for each run. These values are not normally user-adjustable.

| Generated Value | ComfyUI Node ID | Node Class | JSON Field Updated |
|---|---:|---|---|
| General sampler seed | `32` | `KSampler` | `inputs.seed` |
| Flux random noise seed | `26` | `RandomNoise` | `inputs.noise_seed` |
| Body enhancement seed | `52` | `FaceDetailerPipe` | `inputs.seed` |
| Face enhancement seed | `54` | `FaceDetailerPipe` | `inputs.seed` |

## Mask Routing Behavior

The mask route is controlled by `has_drawn_mask`.

| Client State | Connection Applied | Meaning |
|---|---|---|
| User draws a mask | `13.inputs.mask = ["88", 0]` | Use the user-drawn mask. |
| User does not draw a mask | `13.inputs.mask = ["85", 0]` | Use the default generated mask route. |

Relevant nodes:

| Node ID | Purpose |
|---:|---|
| `13` | `InpaintCropImproved`; receives the active mask. |
| `85` | Default mask route. |
| `88` | Drawn mask route. |

## Connection Rule Format

In ComfyUI API JSON, node links are stored on the target node input.

For example:

```json
{
  "83": {
    "inputs": {
      "images": ["82", 0]
    }
  }
}
```

This means:

```text
83.images <- 82
```

The tables below use the same shorthand:

```text
target_node.input <- source_node
```

## Checkbox Routing Matrix

The three main checkboxes are routing controls:

- `general_enhance`
- `advance_details`
- `body_enhance`

They should not be implemented as simple boolean fields inside the ComfyUI JSON. They modify workflow connections.

| Case | `general_enhance` | `advance_details` | `body_enhance` | Workflow Path |
|---:|---:|---:|---:|---|
| 1 | `true` | `false` | `false` | General enhancement only |
| 2 | `false` | `true` | `false` | Advance details only |
| 3 | `false` | `false` | `true` | Body/face enhancement only |
| 4 | `true` | `true` | `false` | General enhancement, then advance details |
| 5 | `true` | `false` | `true` | General enhancement, then body/face enhancement |
| 6 | `false` | `true` | `true` | Advance details, then body/face enhancement |
| 7 | `true` | `true` | `true` | General enhancement, then advance details, then body/face enhancement |
| 8 | `false` | `false` | `false` | Save original image |

## Detailed Connection Behavior

### Case 1: Only General Enhancement

Client input:

```json
{
  "general_enhance": true,
  "advance_details": false,
  "body_enhance": false
}
```

Connections:

```text
66.image <- 79
12.images <- 64
83.images <- 82
```

Expected result:

- Run the general enhancement branch.
- Stitch the result.
- Save the stitched output from node `82`.

### Case 2: Only Advance Details

Client input:

```json
{
  "general_enhance": false,
  "advance_details": true,
  "body_enhance": false
}
```

Connections:

```text
69.image <- 79
12.images <- 21
83.images <- 82
```

Expected result:

- Skip general enhancement.
- Run the advanced Flux detail branch.
- Stitch the result.
- Save the stitched output from node `82`.

### Case 3: Only Body Enhancement

Client input:

```json
{
  "general_enhance": false,
  "advance_details": false,
  "body_enhance": true
}
```

Connections:

```text
53.image <- 63
83.images <- 54
30.text_c = ""
```

Expected result:

- Send the original input image directly to body enhancement resize node `53`.
- Save final face/body output from node `54`.
- Disconnect Qwen caption output from `30.text_c`, because the caption path is not needed for body-only mode.

### Case 4: General Enhancement + Advance Details

Client input:

```json
{
  "general_enhance": true,
  "advance_details": true,
  "body_enhance": false
}
```

Connections:

```text
66.image <- 79
69.image <- 64
12.images <- 21
83.images <- 82
```

Expected result:

- Run general enhancement first.
- Feed the general enhancement output into the advanced detail branch.
- Stitch and save node `82`.

### Case 5: General Enhancement + Body Enhancement

Client input:

```json
{
  "general_enhance": true,
  "advance_details": false,
  "body_enhance": true
}
```

Connections:

```text
66.image <- 79
12.images <- 64
53.image <- 82
83.images <- 54
```

Expected result:

- Run general enhancement.
- Stitch the general enhancement result.
- Send stitched result to body/face enhancement.
- Save final output from node `54`.

### Case 6: Advance Details + Body Enhancement

Client input:

```json
{
  "general_enhance": false,
  "advance_details": true,
  "body_enhance": true
}
```

Connections:

```text
69.image <- 79
12.images <- 21
53.image <- 82
83.images <- 54
```

Expected result:

- Run advanced detail branch.
- Stitch the advanced detail result.
- Send stitched result to body/face enhancement.
- Save final output from node `54`.

### Case 7: All Enabled

Client input:

```json
{
  "general_enhance": true,
  "advance_details": true,
  "body_enhance": true
}
```

Connections:

```text
66.image <- 79
69.image <- 64
12.images <- 21
53.image <- 82
83.images <- 54
```

Expected result:

- Run general enhancement.
- Feed general enhancement output into advanced details.
- Stitch the advanced detail result.
- Send stitched result to body/face enhancement.
- Save final output from node `54`.

### Case 8: None Enabled

Client input:

```json
{
  "general_enhance": false,
  "advance_details": false,
  "body_enhance": false
}
```

Connections:

```text
83.images <- 63
```

Expected result:

- Skip enhancement branches.
- Save the original input image.

## Node Reference

| Node ID | Node Class / Title | Workflow Role |
|---:|---|---|
| `12` | `easy imageListToImageBatch` | Converts processed images back into a batch for stitching. |
| `13` | `InpaintCropImproved` | Crops/inpaints tiles using the selected mask. |
| `21` | `VAEDecode` | Decodes advanced Flux output. |
| `23` | `BasicScheduler` | Advanced detail scheduler and denoise control. |
| `26` | `RandomNoise` | Advanced detail random noise seed. |
| `30` | `StringFunction\|pysssss` | Merges prompt text and Qwen caption text. |
| `32` | `KSampler` | General enhancement sampler. |
| `33` | `AILab_QwenVL_GGUF` | Qwen image caption/prompt helper. |
| `35` | `StringFunction\|pysssss` | User prompt and default style prompt composition. |
| `37` | `LoraLoader` | Detail LoRA strength. |
| `52` | `FaceDetailerPipe` | Body/person enhancement pass. |
| `53` | `ImageResize+` | Prepares image for body/face enhancement. |
| `54` | `FaceDetailerPipe` | Face enhancement pass and final body branch output. |
| `63` | `ETN_LoadImageBase64` | Main input image. |
| `64` | `VAEDecode` | General enhancement decoded output. |
| `66` | `ImagePass` | General enhancement pass-through node. |
| `69` | `ImagePass` | Advanced detail pass-through node. |
| `74` | `Blend` | Blend/sharpen control. |
| `79` | `ImageResize+` | Advanced/general preparation resize. |
| `82` | `InpaintStitchImproved` | Stitches processed tiles into the final image before optional body enhancement. |
| `83` | `SaveImage` | Final output node. |
| `85` | `MaskComposite` | Default mask route. |
| `86` | `ETN_LoadImageBase64` | Mask input image. |
| `88` | `ImageToMask` | Drawn mask route. |

## Recommended API Payload

The new website can send a payload similar to this:

```json
{
  "image_b64": "base64-image-data",
  "mask_b64": "base64-mask-data",
  "has_drawn_mask": false,
  "custom_prompt": "Improve realism and preserve the original design.",
  "general_enhance": true,
  "details": 1.0,
  "general_denoise": 0.1,
  "advance_details": false,
  "additional_detail_pass": 0.35,
  "sharpen": 0.4,
  "body_enhance": false,
  "body_enhancement_denoise": 0.2,
  "face_enhancement_denoise": 0.2,
  "workflow_debug": false,
  "workflow": "General_Enhancement_v04"
}
```

## Backend Implementation Checklist

1. Load the base ComfyUI API JSON workflow.
2. Set `63.inputs.image` from `image_b64`.
3. Set `86.inputs.image` from `mask_b64`.
4. Route `13.inputs.mask` to node `88` when `has_drawn_mask` is true; otherwise route it to node `85`.
5. Set prompt text on nodes `35` and `33`.
6. Generate fresh seeds for nodes `32`, `26`, `52`, and `54`.
7. Apply slider values to nodes `37`, `32`, `23`, `74`, `52`, and `54`.
8. Apply checkbox routing by changing node connections according to the routing matrix.
9. Submit the final mutated workflow JSON to ComfyUI or RunPod.
10. If `workflow_debug` is enabled for an admin user, save the final JSON payload for debugging.

## Backend Pseudocode

```python
prompt["63"]["inputs"]["image"] = image_b64
prompt["86"]["inputs"]["image"] = mask_b64

if has_drawn_mask:
    prompt["13"]["inputs"]["mask"] = ["88", 0]
else:
    prompt["13"]["inputs"]["mask"] = ["85", 0]

cleaned_prompt = (custom_prompt or "").strip()
prompt["35"]["inputs"]["text_a"] = cleaned_prompt
prompt["33"]["inputs"]["custom_prompt"] = cleaned_prompt

prompt["32"]["inputs"]["seed"] = random_seed()
prompt["26"]["inputs"]["noise_seed"] = random_seed()
prompt["52"]["inputs"]["seed"] = random_seed()
prompt["54"]["inputs"]["seed"] = random_seed()

prompt["37"]["inputs"]["strength_model"] = float(details)
prompt["32"]["inputs"]["denoise"] = float(general_denoise)
prompt["23"]["inputs"]["denoise"] = float(additional_detail_pass)
prompt["74"]["inputs"]["blend_factor"] = float(sharpen)
prompt["52"]["inputs"]["denoise"] = float(body_enhancement_denoise)
prompt["54"]["inputs"]["denoise"] = float(face_enhancement_denoise)

apply_branch_routing(
    prompt,
    general_enhance=general_enhance,
    advance_details=advance_details,
    body_enhance=body_enhance,
)
```

## Validation Notes

The backend should validate incoming values before mutating the workflow.

Recommended validation:

| Field | Validation |
|---|---|
| `image_b64` | Required non-empty string. |
| `mask_b64` | Required string; may be generated by backend when no mask is drawn. |
| `custom_prompt` | Trim whitespace; allow empty string. |
| `details` | Clamp to `0.0` through `2.0`. |
| `general_denoise` | Clamp to `0.0` through `0.45`. |
| `additional_detail_pass` | Clamp to `0.0` through `0.7`. |
| `sharpen` | Clamp to `0.0` through `1.0`. |
| `body_enhancement_denoise` | Clamp to `0.0` through `0.3`. |
| `face_enhancement_denoise` | Clamp to `0.0` through `0.3`. |
| Checkbox fields | Coerce to boolean. |

## Important Notes

- Checkbox values control node routing, not simple ComfyUI boolean inputs.
- Slider values directly update ComfyUI node input fields.
- The final output node is always node `83`, but its `images` input changes depending on the selected workflow path.
- When body enhancement is enabled, the final saved image should come from node `54`.
- When body enhancement is not enabled, the final saved image usually comes from node `82`, except when all enhancement branches are disabled.
- In body-only mode, Qwen caption output should be disconnected from node `30.text_c`.
