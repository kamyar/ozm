import Foundation

struct AgentInfo: Codable, Sendable {
    let name: String
    let description: String
}

struct ApprovalRequest: Codable, Sendable {
    let version: Int
    let id: String
    let type: RequestType
    let agent: AgentInfo
    let payload: Payload

    enum RequestType: String, Codable, Sendable {
        case fileApproval = "file_approval"
        case cmdApproval = "cmd_approval"
        case override
        case status
    }

    struct Payload: Codable, Sendable {
        // file_approval
        var script: String?
        var label: String?
        var content: String?
        var diff: String?
        var lineCount: Int?
        var syntax: String?

        // cmd_approval + override
        var command: String?

        // override only
        var violation: String?
        var reason: String?

        enum CodingKeys: String, CodingKey {
            case script, label, content, diff
            case lineCount = "line_count"
            case syntax, command, violation, reason
        }
    }
}

struct ApprovalResponse: Codable, Sendable {
    let version: Int
    let id: String
    let decision: Decision
    var feedback: String?
    var command: String?
    var allowPattern: String?
    var blockPattern: String?
    var applyGlobally: Bool

    enum Decision: String, Codable, Sendable {
        case allow
        case deny
        case error
        case status
    }

    enum CodingKeys: String, CodingKey {
        case version, id, decision, feedback, command
        case allowPattern = "allow_pattern"
        case blockPattern = "block_pattern"
        case applyGlobally = "apply_globally"
    }

    init(
        id: String,
        decision: Decision,
        feedback: String? = nil,
        command: String? = nil,
        allowPattern: String? = nil,
        blockPattern: String? = nil,
        applyGlobally: Bool = false
    ) {
        self.version = 1
        self.id = id
        self.decision = decision
        self.feedback = feedback
        self.command = command
        self.allowPattern = allowPattern
        self.blockPattern = blockPattern
        self.applyGlobally = applyGlobally
    }
}
