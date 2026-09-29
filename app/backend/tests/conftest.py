"""Keep the portable CI suite runnable without the Apple Silicon MLX runtime."""
import platform

# These tests import the native inference modules. Run the complete suite on the
# target Mac with script/known_good.sh quick; Linux CI checks the wire contracts.
collect_ignore = []
if platform.system() != "Darwin" or platform.machine() != "arm64":
    collect_ignore = [
        "test_caption_accuracy.py",
        "test_chunk_rollover.py",
        "test_streaming_stalls.py",
        "test_ast_streaming.py",
        "test_preview_scheduling.py",
        "test_segmenter_boundaries.py",
        "test_gemma_only.py",
        "test_worker_lifecycle.py",
    ]
