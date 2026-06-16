#!/usr/bin/env ruby
# frozen_string_literal: true

require "xcodeproj"
require "fileutils"

ROOT = File.expand_path("..", __dir__)
PROJECT_PATH = File.join(ROOT, "WeeOrchestrator.xcodeproj")
APP_DIR = File.join(ROOT, "WeeOrchestrator")

FileUtils.rm_rf(PROJECT_PATH)

project = Xcodeproj::Project.new(PROJECT_PATH)
target = project.new_target(:application, "WeeOrchestrator", :ios, "17.0")

project.build_configurations.each do |config|
  config.build_settings["IPHONEOS_DEPLOYMENT_TARGET"] = "17.0"
  config.build_settings["SDKROOT"] = "iphoneos"
  config.build_settings["SUPPORTED_PLATFORMS"] = "iphoneos iphonesimulator"
  config.build_settings["SWIFT_VERSION"] = "6.0"
end

target.build_configurations.each do |config|
  settings = config.build_settings
  settings["PRODUCT_BUNDLE_IDENTIFIER"] = "com.lipkey.weeorchestrator"
  settings["PRODUCT_NAME"] = "WeeOrchestrator"
  settings["INFOPLIST_FILE"] = "WeeOrchestrator/Info.plist"
  settings["GENERATE_INFOPLIST_FILE"] = "NO"
  settings["SWIFT_VERSION"] = "6.0"
  settings["IPHONEOS_DEPLOYMENT_TARGET"] = "17.0"
  settings["SDKROOT"] = "iphoneos"
  settings["SUPPORTED_PLATFORMS"] = "iphoneos iphonesimulator"
  settings["SUPPORTS_MACCATALYST"] = "NO"
  settings["SUPPORTS_MAC_DESIGNED_FOR_IPHONE_IPAD"] = "NO"
  settings["TARGETED_DEVICE_FAMILY"] = "1,2"
  settings["ASSETCATALOG_COMPILER_APPICON_NAME"] = "AppIcon"
  settings["ASSETCATALOG_COMPILER_GLOBAL_ACCENT_COLOR_NAME"] = "AccentColor"
  settings["CODE_SIGN_STYLE"] = "Automatic"
  settings["ENABLE_PREVIEWS"] = "YES"
end

app_group = project.main_group.new_group("WeeOrchestrator", "WeeOrchestrator")

Dir.glob(File.join(APP_DIR, "**", "*")).sort.each do |path|
  next if File.directory?(path)

  relative = path.delete_prefix("#{APP_DIR}/")
  next if relative == "Info.plist"
  next if relative.include?(".xcassets/")

  if path.end_with?(".swift")
    ref = app_group.new_file(relative)
    target.source_build_phase.add_file_reference(ref)
  end
end

assets_ref = app_group.new_file("Assets.xcassets")
target.resources_build_phase.add_file_reference(assets_ref)

project.save
puts "Generated #{PROJECT_PATH}"
