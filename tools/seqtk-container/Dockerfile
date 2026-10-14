# The build script assembles a small context containing verified source archives.
# seqtk is the original Linux program, not the Emscripten port.
ARG BASE_IMAGE=alpine:3.21.3
FROM ${BASE_IMAGE} AS build
RUN apk add --no-cache build-base
COPY seqtk.tar.gz zlib.tar.gz /sources/
RUN mkdir -p /src/seqtk /src/zlib /out/licenses && \
    tar -xzf /sources/seqtk.tar.gz -C /src/seqtk --strip-components=1 && \
    tar -xzf /sources/zlib.tar.gz -C /src/zlib --strip-components=1 && \
    cd /src/zlib && ./configure --static --prefix=/opt/zlib && \
    make -j2 && make install && \
    cc -O3 -I/opt/zlib/include /src/seqtk/seqtk.c /opt/zlib/lib/libz.a -lm -o /out/seqtk && \
    cp /src/seqtk/LICENSE /out/licenses/seqtk.txt && \
    cp /src/zlib/LICENSE /out/licenses/zlib.txt

FROM ${BASE_IMAGE}
COPY --from=build /out/seqtk /usr/local/bin/seqtk
COPY --from=build /out/licenses /usr/share/licenses/seqtk
COPY runner.sh /usr/local/bin/run-seqtk
RUN chmod 755 /usr/local/bin/run-seqtk && mkdir -p /work
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/run-seqtk"]
