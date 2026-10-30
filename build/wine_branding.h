// Required imports for this function and its two private helpers:
#import <Foundation/Foundation.h>
#include <mach-o/loader.h>
#include <mach/machine.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <string.h>
#include <stdio.h>

static int brandWineError(NSString *message) {
    fprintf(stderr, "Cannot brand Wine: %s\n", message.UTF8String ?: "unknown error");
    return 1;
}

// Remove formatting whitespace only from plist containers, never from values.
static void brandWineCompactXML(NSXMLElement *element) {
    BOOL container = [@[@"plist", @"dict", @"array"] containsObject:element.name];
    for (NSXMLNode *child in [element.children copy]) {
        if (container && child.kind == NSXMLTextKind &&
            [[child.stringValue stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] length] == 0) {
            [child detach];
        } else if (child.kind == NSXMLElementKind) {
            brandWineCompactXML((NSXMLElement *)child);
        }
    }
}

// The destination must not exist. Only that new file is written; sign it afterwards.
static int brandWine(const char *source, const char *destination) {
    @autoreleasepool {
        if (!source || !destination) return brandWineError(@"missing source or destination");
        NSString *sourcePath = [[NSFileManager defaultManager] stringWithFileSystemRepresentation:source length:strlen(source)];
        if (!sourcePath) return brandWineError(@"invalid source path");
        struct stat sourceStat;
        if (lstat(source, &sourceStat) || !S_ISREG(sourceStat.st_mode))
            return brandWineError(@"source must be a regular file, not a symbolic link");
        NSError *error = nil;
        NSData *sourceData = [NSData dataWithContentsOfFile:sourcePath options:0 error:&error];
        if (!sourceData) return brandWineError(error.localizedDescription ?: @"cannot read source");
        NSMutableData *data = [sourceData mutableCopy];
        NSUInteger length = data.length;
        if (length < sizeof(struct mach_header_64)) return brandWineError(@"truncated Mach-O header");
        const uint8_t *bytes = data.bytes;
        struct mach_header_64 header;
        memcpy(&header, bytes, sizeof(header));
        if (header.magic != MH_MAGIC_64 || header.cputype != CPU_TYPE_X86_64 || header.filetype != MH_EXECUTE)
            return brandWineError(@"expected a thin little-endian x86_64 Mach-O executable");
        if ((uint64_t)header.sizeofcmds > length - sizeof(header) ||
            header.ncmds > header.sizeofcmds / sizeof(struct load_command))
            return brandWineError(@"invalid Mach-O command bounds");
        NSUInteger commandEnd = sizeof(header) + header.sizeofcmds;
        NSUInteger cursor = sizeof(header), plistOffset = 0, plistSize = 0;
        BOOL found = NO;
        for (uint32_t i = 0; i < header.ncmds; ++i) {
            if (cursor > commandEnd || commandEnd - cursor < sizeof(struct load_command))
                return brandWineError(@"truncated load command");
            struct load_command command;
            memcpy(&command, bytes + cursor, sizeof(command));
            if (command.cmdsize < sizeof(command) || command.cmdsize % 8 || command.cmdsize > commandEnd - cursor)
                return brandWineError(@"invalid load command size");
            if (command.cmd == LC_SEGMENT_64) {
                if (command.cmdsize < sizeof(struct segment_command_64))
                    return brandWineError(@"truncated segment command");
                struct segment_command_64 segment;
                memcpy(&segment, bytes + cursor, sizeof(segment));
                if (segment.nsects > (command.cmdsize - sizeof(segment)) / sizeof(struct section_64))
                    return brandWineError(@"section table exceeds segment command");
                for (uint32_t j = 0; j < segment.nsects; ++j) {
                    struct section_64 section;
                    memcpy(&section, bytes + cursor + sizeof(segment) + (NSUInteger)j * sizeof(section), sizeof(section));
                    if (strncmp(segment.segname, "__TEXT", sizeof(segment.segname)) ||
                        strncmp(section.segname, "__TEXT", sizeof(section.segname)) ||
                        strncmp(section.sectname, "__info_plist", sizeof(section.sectname))) continue;
                    if (found) return brandWineError(@"duplicate embedded Info.plist sections");
                    if (!section.size || section.offset < commandEnd || section.offset > length || section.size > length - section.offset ||
                        segment.fileoff > length || segment.filesize > length - segment.fileoff ||
                        section.offset < segment.fileoff || section.size > segment.filesize ||
                        (uint64_t)section.offset - segment.fileoff > segment.filesize - section.size ||
                        (section.flags & SECTION_TYPE) != S_REGULAR)
                        return brandWineError(@"invalid embedded Info.plist section bounds or type");
                    plistOffset = section.offset;
                    plistSize = (NSUInteger)section.size;
                    found = YES;
                }
            }
            cursor += command.cmdsize;
        }
        if (cursor != commandEnd || !found) return brandWineError(@"missing Info.plist or inconsistent load commands");
        NSUInteger used = plistSize;
        while (used && bytes[plistOffset + used - 1] == 0) --used;
        NSData *plistData = [NSData dataWithBytes:bytes + plistOffset length:used];
        id plist = [NSPropertyListSerialization propertyListWithData:plistData options:NSPropertyListMutableContainers format:NULL error:&error];
        if (![plist isKindOfClass:NSMutableDictionary.class])
            return brandWineError(error.localizedDescription ?: @"embedded Info.plist is not a dictionary");
        NSMutableDictionary *info = plist;
        info[@"CFBundleName"] = @"Paint.NET";
        info[@"CFBundleDisplayName"] = @"Paint.NET";
        info[@"CFBundleIdentifier"] = @"org.paintdotnet.wine.macos.editor";
        info[@"CFBundleExecutable"] = @"wine";
        NSData *xml = [NSPropertyListSerialization dataWithPropertyList:info format:NSPropertyListXMLFormat_v1_0 options:0 error:&error];
        if (!xml) return brandWineError(error.localizedDescription ?: @"cannot serialize Info.plist");
        NSXMLDocument *document = [[NSXMLDocument alloc] initWithData:xml options:0 error:&error];
        if (!document.rootElement) return brandWineError(error.localizedDescription ?: @"cannot compact Info.plist XML");
        brandWineCompactXML(document.rootElement);
        xml = [document XMLDataWithOptions:NSXMLNodeCompactEmptyElement];
        id verified = [NSPropertyListSerialization propertyListWithData:xml options:0 format:NULL error:&error];
        if (![verified isEqual:info]) return brandWineError(@"compacted Info.plist changed its values");
        if (xml.length > plistSize) return brandWineError(@"new Info.plist does not fit the existing Mach-O section");
        uint8_t *mutableBytes = data.mutableBytes;
        memset(mutableBytes + plistOffset, 0, plistSize);
        memcpy(mutableBytes + plistOffset, xml.bytes, xml.length);
        int fd = open(destination, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, sourceStat.st_mode & 0777);
        if (fd < 0) return brandWineError([NSString stringWithFormat:@"cannot create destination exclusively: %s", strerror(errno)]);
        BOOL success = YES;
        int savedError = 0;
        NSUInteger written = 0;
        while (written < length) {
            size_t chunk = (size_t)MIN(length - written, (NSUInteger)(1024 * 1024));
            ssize_t result = write(fd, mutableBytes + written, chunk);
            if (result < 0 && errno == EINTR) continue;
            if (result <= 0) { savedError = result < 0 ? errno : EIO; success = NO; break; }
            written += (NSUInteger)result;
        }
        if (success && fchmod(fd, sourceStat.st_mode & 0777) != 0) { savedError = errno; success = NO; }
        if (close(fd) != 0 && success) { savedError = errno; success = NO; }
        if (!success) {
            unlink(destination);
            return brandWineError([NSString stringWithFormat:@"cannot write destination: %s", strerror(savedError)]);
        }
        return 0;
    }
}
