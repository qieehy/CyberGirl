"""Explicit WCDB text metadata; no guessed codecs, dictionaries or lossy decode."""

from ...social.models import SocialError, text_value

MAX_TEXT_BYTES = 16000


def decode_text(content, compression_type):
    # Tencent WCDB CompressionConst.hpp: type = algorithm << 1 | is_blob.
    # 0/1 are uncompressed; 2/3 require an application-owned dictionary;
    # 4/5 are ordinary Zstandard. Only original TEXT is supported here.
    if compression_type is None:
        compression_type = 0  # Uncompressed legacy table / nullable new column.
    if type(compression_type) is not int or compression_type not in {0, 1, 4}:
        raise SocialError("text_compression_unsupported")
    if compression_type == 4:
        if not isinstance(content, bytes) or not 1 <= len(content) <= MAX_TEXT_BYTES:
            raise SocialError("compressed_text_bound")
        import zstandard as zstd

        try:
            params = zstd.get_frame_parameters(content)
            # Check advertised sizes BEFORE the native decompressor allocates.
            if (params.dict_id or params.window_size > 1024 * 1024
                    or params.content_size not in {zstd.CONTENTSIZE_UNKNOWN}
                    and params.content_size > MAX_TEXT_BYTES):
                raise SocialError("compressed_text_bound")
            content = zstd.ZstdDecompressor(max_window_size=1024 * 1024).decompress(
                content, max_output_size=MAX_TEXT_BYTES, allow_extra_data=False)
        except zstd.ZstdError:
            raise SocialError("compressed_text_invalid") from None
    if isinstance(content, bytes):
        if len(content) > MAX_TEXT_BYTES:
            raise SocialError("text_size_bound")
        try:
            content = content.decode("utf-8", errors="strict")
        except UnicodeError:
            raise SocialError("text_encoding_unsupported") from None
    text_value(content, 4000)
    return content
