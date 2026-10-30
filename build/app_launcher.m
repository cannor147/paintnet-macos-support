#import <Cocoa/Cocoa.h>
#include <sys/stdio.h>
#include <errno.h>
#include <stdio.h>
#include "wine_branding.h"

@interface Launcher : NSObject <NSApplicationDelegate>
@property NSString *installation;
@property NSTask *editor;
@property BOOL quitting;
@end

@implementation Launcher
- (void)activateEditor {
    if (self.editor.running) {
        NSRunningApplication *app = [NSRunningApplication runningApplicationWithProcessIdentifier:self.editor.processIdentifier];
        [app activateFromApplication:[NSRunningApplication currentApplication] options:0];
    }
}
- (void)launchFiles:(NSArray<NSString *> *)files {
    if (!self.installation.length) return;
    if (self.editor.running && !files.count) { [self activateEditor]; return; }
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:[self.installation stringByAppendingPathComponent:@"bin/launch"]];
    task.arguments = files;
    NSMutableDictionary *environment = [[[NSProcessInfo processInfo] environment] mutableCopy];
    environment[@"PDN_WINE_LOADER"] = [[[NSBundle mainBundle] executablePath].stringByDeletingLastPathComponent
                                       stringByAppendingPathComponent:@"wine"];
    task.environment = environment;
    BOOL primary = !self.editor.running;
    if (primary) {
        self.editor = task;
        __weak Launcher *weakSelf = self;
        task.terminationHandler = ^(NSTask *finished) {
            dispatch_async(dispatch_get_main_queue(), ^{
                Launcher *owner = weakSelf;
                if (!owner || owner.editor != finished) return;
                if (owner.quitting) [NSApp replyToApplicationShouldTerminate:YES];
                else [NSApp terminate:nil];
            });
        };
    }
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        if (primary) self.editor = nil;
        NSAlert *alert = [NSAlert new];
        alert.messageText = @"Paint.NET could not start";
        alert.informativeText = error.localizedDescription;
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{ [self activateEditor]; });
}
- (void)applicationWillFinishLaunching:(NSNotification *)notification {
    (void)notification;
    NSURL *resource = [[NSBundle mainBundle] URLForResource:@"installation" withExtension:@"txt"];
    self.installation = [[NSString stringWithContentsOfURL:resource encoding:NSUTF8StringEncoding error:nil]
                         stringByTrimmingCharactersInSet:[NSCharacterSet newlineCharacterSet]];
    NSMenu *menu = [NSMenu new];
    NSMenuItem *item = [NSMenuItem new];
    NSMenu *appMenu = [[NSMenu alloc] initWithTitle:@"Paint.NET"];
    [appMenu addItemWithTitle:@"Quit Paint.NET" action:@selector(terminate:) keyEquivalent:@"q"];
    item.submenu = appMenu;
    [menu addItem:item];
    NSApp.mainMenu = menu;
}
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    if (!self.installation.length) {
        NSAlert *alert = [NSAlert new];
        alert.messageText = @"Paint.NET installation was not found";
        [alert runModal];
        [NSApp terminate:nil];
        return;
    }
    // Finder sends openFiles before didFinishLaunching for a document launch.
    if (!self.editor) [self launchFiles:@[]];
}
- (void)application:(NSApplication *)application openFiles:(NSArray<NSString *> *)files {
    [self launchFiles:files];
    [application replyToOpenOrPrint:NSApplicationDelegateReplySuccess];
}
- (BOOL)applicationShouldHandleReopen:(NSApplication *)application hasVisibleWindows:(BOOL)visible {
    (void)application; (void)visible;
    [self launchFiles:@[]];
    return NO;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)application {
    (void)application;
    if (!self.editor.running) return NSTerminateNow;
    self.quitting = YES;
    // Let Wine show its normal unsaved-work prompt; never kill the editor here.
    NSRunningApplication *app = [NSRunningApplication runningApplicationWithProcessIdentifier:self.editor.processIdentifier];
    if (![app terminate]) { self.quitting = NO; return NSTerminateCancel; }
    return NSTerminateLater;
}
@end

int main(int argc, const char **argv) {
    if (argc == 4 && strcmp(argv[1], "--brand-wine") == 0)
        return brandWine(argv[2], argv[3]);
    if (argc == 4 && strcmp(argv[1], "--rename-exclusive") == 0) {
        if (!renamex_np(argv[2], argv[3], RENAME_EXCL)) return 0;
        perror("Cannot commit installation");
        return 1;
    }
    @autoreleasepool {
        NSApplication *app = [NSApplication sharedApplication];
        Launcher *delegate = [Launcher new];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
