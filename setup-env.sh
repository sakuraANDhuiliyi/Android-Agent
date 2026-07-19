#!/usr/bin/env bash
# Source before manual gradle/adb:  source "./setup-env.sh"
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
echo "JAVA_HOME=$JAVA_HOME"
echo "ANDROID_HOME=$ANDROID_HOME"
java -version 2>&1 | head -1
command -v adb >/dev/null && adb version 2>&1 | head -1 || echo "adb not found under ANDROID_HOME"
