## Pretraining Reading

### Foundation Model Pretraining and Scaling

- [FineWeb](https://huggingface.co/spaces/HuggingFaceFW/blogpost-fineweb-v1) · Web data filtering, deduplication, and ablations.
- [LLMs From Scratch](https://sebastianraschka.com/llms-from-scratch/) · Build and pretrain a small transformer with code.
- [Pre-Training Without Learning Rate Decay](https://sebastianraschka.com/blog/2026/llm-pretraining-without-learning-rate-decay.html) · A November 2026 comparison of learning-rate schedules and checkpoint averaging.
- [Chinchilla](https://arxiv.org/abs/2203.15556) · A reference study on allocating training compute between parameters and tokens.
- [The Ultra-Scale Playbook](https://huggingface.co/spaces/nanotron/ultrascale-playbook) · Distributed training, parallelism, and memory trade-offs.
- [TorchTitan 1T MoE case study](https://pytorch.org/blog/training-a-1t-parameter-moe-on-eu-public-infrastructure-with-pytorch-and-torchtitan/) · Large-scale training in practice. See the [PyTorch Distributed DevLog](https://docs.pytorch.org/devlogs/distributed/) for implementation updates.
- [Ai2 Olmo](https://allenai.org/olmo) · Open training data, intermediate checkpoints, and [Olmo-core training code](https://github.com/allenai/Olmo-core).

### Multimodal and World Model Pretraining

- [Meta V-JEPA 2](https://ai.meta.com/blog/v-jepa-2-world-model-benchmarks/) · Action-free video pretraining and subsequent action-conditioned learning.
- [Transfusion from scratch](https://arxiv.org/abs/2603.03276) · Controlled pretraining experiments across text, images, video, and action-conditioned video.
- [NVIDIA NeMo video training pipeline](https://developer.nvidia.com/blog/accelerate-custom-video-foundation-model-pipelines-with-new-nvidia-nemo-framework-capabilities/) · Video curation and data loading for foundation model training.
- [NVIDIA Cosmos 3](https://research.nvidia.com/labs/cosmos-lab/cosmos3/) · An omnimodal foundation model. The [interactive simulation blog](https://developer.nvidia.com/blog/build-world-models-for-interactive-simulation-with-nvidia-cosmos) shows its November 2026 adaptation to driving and robotics simulation.
- [Runway Praxis-1](https://runway.com/research/praxis-1-video-pretraining-to-robot-control) · A November 2026 report on transferring pretrained video features to robot control.
