import SwiftUI

struct NewTaskView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var prompt: String = ""
    @State private var selectedAgent: String = ""
    @State private var runtime: String = ""
    @State private var model: String = ""
    @State private var timeoutText: String = ""

    @State private var isSubmitting = false
    @State private var errorMessage: String?
    @State private var createdTask: CreateBackgroundTaskResponse?

    var body: some View {
        Form {
            Section("Prompt") {
                TextEditor(text: $prompt)
                    .frame(minHeight: 120)
            }

            Section("Agent (optional)") {
                Picker("Agent", selection: $selectedAgent) {
                    Text("Default").tag("")
                    ForEach(appState.agents) { agent in
                        Text(agent.name).tag(agent.name)
                    }
                }
            }

            Section("Overrides (optional)") {
                TextField("Runtime, e.g. claude", text: $runtime)
                    .autocorrectionDisabled()
                TextField("Model, e.g. claude-sonnet-4-6", text: $model)
                    .autocorrectionDisabled()
                TextField("Timeout (seconds)", text: $timeoutText)
                    .keyboardType(.numberPad)
            }

            if let errorMessage {
                Section {
                    Text(errorMessage)
                        .foregroundStyle(WeeTheme.danger)
                }
            }

            if let createdTask {
                Section("Created") {
                    Text("Task \(createdTask.taskId) — \(createdTask.status)")
                        .foregroundStyle(WeeTheme.accent)
                }
            }

            Section {
                Button(action: submit) {
                    if isSubmitting {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                    } else {
                        Text("Create Background Task")
                            .frame(maxWidth: .infinity)
                            .fontWeight(.semibold)
                    }
                }
                .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isSubmitting)
                .listRowBackground(WeeTheme.accent)
                .foregroundStyle(.black)
            }
        }
        .navigationTitle("New Task")
        .navigationBarTitleDisplayMode(.inline)
        .scrollContentBackground(.hidden)
        .weeBackground()
    }

    private func submit() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                let timeout = Int(timeoutText)
                let response = try await appState.createBackgroundTask(
                    prompt: prompt.trimmingCharacters(in: .whitespacesAndNewlines),
                    agent: selectedAgent.isEmpty ? nil : selectedAgent,
                    runtime: runtime.isEmpty ? nil : runtime,
                    model: model.isEmpty ? nil : model,
                    timeout: timeout
                )
                createdTask = response
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
