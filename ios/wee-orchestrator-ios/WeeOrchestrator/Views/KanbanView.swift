import SwiftUI
import UIKit

struct KanbanView: View {
    @Bindable var model: WeeAppModel
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @State private var selectedColumn: KanbanColumnID = .todo
    @State private var selectedCard: KanbanCard?

    private var isPadLayout: Bool {
        horizontalSizeClass == .regular
    }

    private var board: KanbanBoardResponse? {
        model.kanbanBoard
    }

    private var dueCount: Int {
        board?.dueCards.count ?? 0
    }

    var body: some View {
        VStack(spacing: 12) {
            header

            if isPadLayout {
                padBoard
            } else {
                phoneBoard
            }
        }
        .padding(UIDevice.current.userInterfaceIdiom == .pad ? 0 : 12)
        .task {
            await model.loadKanbanBoard()
        }
        .sheet(item: $selectedCard) { card in
            KanbanItemDetailSheet(model: model, card: card)
        }
    }

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 5) {
                Text("Kanban")
                    .font(.title3.weight(.bold))
                    .foregroundStyle(WeeTheme.textPrimary)
                HStack {
                    StatusPill(text: "\(board?.total ?? 0) cards", color: WeeTheme.accent, symbol: "rectangle.3.group")
                    StatusPill(text: "\(dueCount) due", color: dueCount > 0 ? WeeTheme.gold : WeeTheme.textSecondary, symbol: "bell.badge")
                    if let repo = board?.repo, repo.isEmpty == false {
                        StatusPill(text: "GitHub", color: WeeTheme.textSecondary, symbol: "number")
                    }
                }
            }
            Spacer()
            Button {
                Task { await model.loadKanbanBoard() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(WeeGhostButtonStyle())
        }
        .padding(14)
        .glassPanel()
    }

    private var phoneBoard: some View {
        VStack(spacing: 12) {
            Picker("Column", selection: $selectedColumn) {
                ForEach(KanbanColumnID.allCases) { column in
                    Text(column.title).tag(column)
                }
            }
            .pickerStyle(.segmented)

            ScrollView {
                VStack(spacing: 12) {
                    dueSection
                    KanbanColumnView(
                        column: selectedColumn,
                        cards: cards(for: selectedColumn),
                        compact: true,
                        onSelect: { selectedCard = $0 }
                    )
                }
            }
        }
    }

    private var padBoard: some View {
        ScrollView {
            VStack(spacing: 12) {
                dueSection

                ScrollView(.horizontal) {
                    HStack(alignment: .top, spacing: 12) {
                        ForEach(KanbanColumnID.allCases) { column in
                            KanbanColumnView(
                                column: column,
                                cards: cards(for: column),
                                compact: false,
                                onSelect: { selectedCard = $0 }
                            )
                            .frame(width: 260)
                        }
                    }
                    .padding(.bottom, 2)
                }
            }
        }
    }

    @ViewBuilder
    private var dueSection: some View {
        let cards = board?.dueCards ?? []
        if cards.isEmpty {
            if let status = model.kanbanStatusMessage {
                EmptyKanbanState(title: status, symbol: "rectangle.stack.badge.minus")
                    .padding(14)
                    .glassPanel()
            }
        } else {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Image(systemName: "bell.badge")
                        .foregroundStyle(WeeTheme.gold)
                    Text("Due Soon")
                        .font(.headline.weight(.semibold))
                        .foregroundStyle(WeeTheme.textPrimary)
                    Spacer()
                    StatusPill(text: "\(cards.count)", color: WeeTheme.gold)
                }

                LazyVStack(spacing: 10) {
                    ForEach(cards.prefix(4)) { card in
                        KanbanCardRow(card: card, compact: true)
                            .contentShape(Rectangle())
                            .onTapGesture {
                                selectedCard = card
                            }
                    }
                }
            }
            .padding(14)
            .glassPanel()
        }
    }

    private func cards(for column: KanbanColumnID) -> [KanbanCard] {
        board?.columns[column.rawValue] ?? []
    }
}

private struct KanbanColumnView: View {
    let column: KanbanColumnID
    let cards: [KanbanCard]
    let compact: Bool
    let onSelect: (KanbanCard) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: column.symbol)
                    .foregroundStyle(WeeTheme.accent)
                Text(column.title)
                    .font(.headline.weight(.semibold))
                    .foregroundStyle(WeeTheme.textPrimary)
                Spacer()
                StatusPill(text: "\(cards.count)", color: WeeTheme.textSecondary)
            }

            if cards.isEmpty {
                EmptyKanbanState(title: "No cards", symbol: "tray")
            } else {
                LazyVStack(spacing: 10) {
                    ForEach(cards) { card in
                        KanbanCardRow(card: card, compact: compact)
                            .contentShape(Rectangle())
                            .onTapGesture {
                                onSelect(card)
                            }
                    }
                }
            }
        }
        .padding(14)
        .glassPanel()
    }
}

private struct KanbanCardRow: View {
    let card: KanbanCard
    let compact: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 8) {
                StatusPill(text: card.source, color: sourceColor, symbol: sourceSymbol)
                if let agent = card.agent, agent.isEmpty == false {
                    Text(agent)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(WeeTheme.textSecondary)
                        .lineLimit(1)
                }
                Spacer()
            }

            Text(card.title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(WeeTheme.textPrimary)
                .lineLimit(compact ? 3 : 4)

            if let due = card.due, due.isEmpty == false {
                StatusPill(text: dueText(due), color: dueColor, symbol: dueSymbol)
            }

            if compact == false, card.details.isEmpty == false {
                Text(card.details)
                    .font(.caption)
                    .foregroundStyle(WeeTheme.textSecondary)
                    .lineLimit(3)
            }

            HStack(spacing: 8) {
                if card.urgency == "urgent" {
                    StatusPill(text: "urgent", color: WeeTheme.danger, symbol: "exclamationmark.triangle.fill")
                } else if card.priority != "normal" {
                    StatusPill(text: card.priority, color: WeeTheme.gold, symbol: "flag.fill")
                }

                if let issue = card.githubIssueNumber {
                    Text("#\(issue)")
                        .font(.caption2.monospaced())
                        .foregroundStyle(WeeTheme.textMuted)
                }

                Spacer()

                if let url = card.url, let link = URL(string: url) {
                    Link(destination: link) {
                        Image(systemName: "arrow.up.right.square")
                    }
                    .foregroundStyle(WeeTheme.accent)
                }
            }
        }
        .padding(12)
        .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).stroke(WeeTheme.glassStroke))
    }

    private var sourceColor: Color {
        card.source == "github" ? WeeTheme.gold : WeeTheme.accent
    }

    private var sourceSymbol: String {
        card.source == "github" ? "number" : "doc.text"
    }

    private var dueColor: Color {
        switch card.dueBucket {
        case "overdue": WeeTheme.danger
        case "today", "soon": WeeTheme.gold
        default: WeeTheme.textSecondary
        }
    }

    private var dueSymbol: String {
        card.dueBucket == "overdue" ? "exclamationmark.circle.fill" : "calendar"
    }

    private func dueText(_ value: String) -> String {
        let short = value.replacingOccurrences(of: "T", with: " ")
            .replacingOccurrences(of: "Z", with: "")
        switch card.dueBucket {
        case "overdue": return "Overdue \(short)"
        case "today": return "Today \(short)"
        case "soon": return "Soon \(short)"
        default: return short
        }
    }
}

private struct KanbanItemDetailSheet: View {
    @Bindable var model: WeeAppModel
    let card: KanbanCard
    @Environment(\.dismiss) private var dismiss

    @State private var detail: KanbanItemDetail?
    @State private var title = ""
    @State private var details = ""
    @State private var status = KanbanColumnID.todo.rawValue
    @State private var agent = ""
    @State private var due = ""
    @State private var priority = "normal"
    @State private var urgency = "normal"
    @State private var comment = ""
    @State private var dispatchAgent = ""
    @State private var dispatchPrompt = ""
    @State private var isWorking = false
    @State private var statusMessage: String?

    private var agentNames: [String] {
        model.agents.map(\.name).filter { !$0.isEmpty }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    summary
                    editor
                    commentPanel
                    dispatchPanel
                    actionPanel
                    commentsPanel
                }
                .padding(16)
            }
            .background(WeeTheme.background.ignoresSafeArea())
            .navigationTitle("TODO")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .task {
                await load()
            }
        }
    }

    private var summary: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                StatusPill(text: detail?.repo ?? "GitHub", color: WeeTheme.gold, symbol: "number")
                if let issue = card.githubIssueNumber {
                    StatusPill(text: "#\(issue)", color: WeeTheme.textSecondary)
                }
                Spacer()
            }

            Text(title.isEmpty ? card.title : title)
                .font(.headline.weight(.semibold))
                .foregroundStyle(WeeTheme.textPrimary)

            if let statusMessage {
                Text(statusMessage)
                    .font(.caption)
                    .foregroundStyle(WeeTheme.accent)
            }
        }
        .padding(14)
        .glassPanel()
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Edit")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(WeeTheme.textPrimary)

            TextField("Title", text: $title)
                .textFieldStyle(.roundedBorder)

            TextEditor(text: $details)
                .frame(minHeight: 120)
                .scrollContentBackground(.hidden)
                .padding(8)
                .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8))

            Picker("Status", selection: $status) {
                ForEach(KanbanColumnID.allCases) { column in
                    Text(column.title).tag(column.rawValue)
                }
            }
            .pickerStyle(.menu)

            TextField("Agent label", text: $agent)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)

            TextField("Due date", text: $due)
                .textFieldStyle(.roundedBorder)
                .textInputAutocapitalization(.never)

            HStack {
                TextField("Priority", text: $priority)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)

                Picker("Urgency", selection: $urgency) {
                    Text("normal").tag("normal")
                    Text("urgent").tag("urgent")
                }
                .pickerStyle(.menu)
            }

            Button {
                Task { await save() }
            } label: {
                Label("Save Changes", systemImage: "square.and.arrow.down")
            }
            .buttonStyle(WeePrimaryButtonStyle())
            .disabled(isWorking || title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(14)
        .glassPanel()
    }

    private var commentPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Comment")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(WeeTheme.textPrimary)
            TextEditor(text: $comment)
                .frame(minHeight: 90)
                .scrollContentBackground(.hidden)
                .padding(8)
                .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8))
            Button {
                Task { await addComment() }
            } label: {
                Label("Add Comment", systemImage: "text.bubble")
            }
            .buttonStyle(WeeGhostButtonStyle())
            .disabled(isWorking || comment.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(14)
        .glassPanel()
    }

    private var dispatchPanel: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Dispatch")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(WeeTheme.textPrimary)

            if agentNames.isEmpty {
                TextField("Agent", text: $dispatchAgent)
                    .textFieldStyle(.roundedBorder)
                    .textInputAutocapitalization(.never)
            } else {
                Picker("Agent", selection: $dispatchAgent) {
                    ForEach(agentNames, id: \.self) { name in
                        Text(name).tag(name)
                    }
                }
                .pickerStyle(.menu)
            }

            TextEditor(text: $dispatchPrompt)
                .frame(minHeight: 80)
                .scrollContentBackground(.hidden)
                .padding(8)
                .background(Color.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 8))

            Button {
                Task { await dispatchItem() }
            } label: {
                Label("Dispatch to Agent", systemImage: "paperplane.fill")
            }
            .buttonStyle(WeePrimaryButtonStyle())
            .disabled(isWorking || dispatchAgent.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(14)
        .glassPanel()
    }

    private var actionPanel: some View {
        HStack(spacing: 10) {
            Button {
                Task { await complete() }
            } label: {
                Label("Complete", systemImage: "checkmark.circle")
            }
            .buttonStyle(WeeGhostButtonStyle())

            Button(role: .destructive) {
                Task { await close() }
            } label: {
                Label("Close", systemImage: "xmark.circle")
            }
            .buttonStyle(WeeGhostButtonStyle())
        }
        .disabled(isWorking)
    }

    @ViewBuilder
    private var commentsPanel: some View {
        let comments = detail?.comments ?? []
        if comments.isEmpty == false {
            VStack(alignment: .leading, spacing: 10) {
                Text("Comments")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(WeeTheme.textPrimary)
                ForEach(comments) { item in
                    VStack(alignment: .leading, spacing: 5) {
                        Text(item.author?.login ?? "comment")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(WeeTheme.textSecondary)
                        Text(item.body)
                            .font(.caption)
                            .foregroundStyle(WeeTheme.textPrimary)
                    }
                    .padding(10)
                    .background(Color.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 8))
                }
            }
            .padding(14)
            .glassPanel()
        }
    }

    private func load() async {
        if dispatchAgent.isEmpty {
            dispatchAgent = card.agent ?? model.selectedAgent
            if dispatchAgent.isEmpty {
                dispatchAgent = agentNames.first ?? ""
            }
        }
        guard let item = await model.loadKanbanItem(id: card.id) else {
            populate(from: card)
            return
        }
        detail = item
        populate(from: item)
    }

    private func populate(from card: KanbanCard) {
        title = card.title
        details = card.details
        status = card.status
        agent = card.agent ?? ""
        due = card.due ?? ""
        priority = card.priority
        urgency = card.urgency
    }

    private func populate(from item: KanbanItemDetail) {
        title = item.title
        details = item.details
        status = item.status
        agent = item.agent ?? ""
        due = item.due ?? ""
        priority = item.priority
        urgency = item.urgency
    }

    private func save() async {
        isWorking = true
        defer { isWorking = false }
        guard let item = await model.updateKanbanItem(
            id: card.id,
            title: title,
            details: details,
            status: status,
            agent: agent,
            due: due,
            priority: priority,
            urgency: urgency
        ) else { return }
        detail = item
        populate(from: item)
        statusMessage = "Saved."
    }

    private func addComment() async {
        isWorking = true
        defer { isWorking = false }
        guard let item = await model.commentKanbanItem(id: card.id, body: comment) else { return }
        detail = item
        comment = ""
        statusMessage = "Comment added."
    }

    private func dispatchItem() async {
        isWorking = true
        defer { isWorking = false }
        guard let response = await model.dispatchKanbanItem(
            id: card.id,
            agent: dispatchAgent,
            prompt: dispatchPrompt
        ) else { return }
        detail = response.item
        populate(from: response.item)
        statusMessage = "Dispatched as \(response.task.taskID)."
    }

    private func complete() async {
        isWorking = true
        defer { isWorking = false }
        guard let item = await model.completeKanbanItem(id: card.id) else { return }
        detail = item
        populate(from: item)
        statusMessage = "Marked complete."
    }

    private func close() async {
        isWorking = true
        defer { isWorking = false }
        guard let item = await model.closeKanbanItem(id: card.id) else { return }
        detail = item
        populate(from: item)
        statusMessage = "Closed."
    }
}

private struct EmptyKanbanState: View {
    let title: String
    let symbol: String

    var body: some View {
        VStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(WeeTheme.textMuted)
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(WeeTheme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, minHeight: 116)
        .padding(18)
    }
}
