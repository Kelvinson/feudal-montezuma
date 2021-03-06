import torch
import torch.nn as nn
import torch.nn.functional as F


class Manager(nn.Module):
	def __init__(self, num_actions):
		super(Manager, self).__init__()
		self.fc = nn.Linear(num_actions*16, num_actions*16)
		# todo: change lstm to dilated lstm
		self.lstm = nn.LSTMCell(num_actions*16, hidden_size=num_actions*16)
		# todo: add lstm initialization
		self.lstm.bias_ih.data.fill_(0)
		self.lstm.bias_hh.data.fill_(0)

		self.fc_value1 = nn.Linear(num_actions*16, 50)
		self.fc_value2 = nn.Linear(50, 1)

	def forward(self, inputs):
		x, (hx, cx) = inputs
		x = F.relu(self.fc(x))
		state = x
		hx, cx = self.lstm(x, (hx, cx))

		goal = hx
		value = F.relu(self.fc_value1(goal))
		value = self.fc_value2(value)

		goal_norm = torch.norm(goal, p=2, dim=1)
		goal = goal.div(goal_norm.detach())
		return goal, (hx, cx), value, state


class Worker(nn.Module):
	def __init__(self, num_actions):
		self.num_actions = num_actions
		super(Worker, self).__init__()

		self.lstm = nn.LSTMCell(num_actions*16, hidden_size=num_actions*16)
		self.lstm.bias_ih.data.fill_(0)
		self.lstm.bias_hh.data.fill_(0)

		
		self.fc = nn.Linear(num_actions*16, 16)

		self.fc_value1 = nn.Linear(num_actions*16, 50)
		self.fc_value2 = nn.Linear(50, 1)
        
		for m in self.modules():
			if isinstance(m, nn.Linear):
				nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')

	def forward(self, inputs):
		x, (hx, cx), goals = inputs
		hx, cx = self.lstm(x, (hx, cx))

		value = F.relu(self.fc_value1(hx))
		value = self.fc_value2(value)

		worker_embed = hx.view(hx.size(0), 
			                   self.num_actions, 
			                   16)

		goals = goals.sum(dim=-1)
		goal_embed = self.fc(goals)
		goal_embed = goal_embed.unsqueeze(-1)
		
		policy = torch.bmm(worker_embed, goal_embed)
		policy = policy.squeeze(-1)
		policy = torch.softmax(policy, dim=-1)
		return policy, (hx, cx), value


class Percept(nn.Module):
	def __init__(self, num_actions):
		super(Percept, self).__init__()
		self.conv1 = nn.Conv2d(
			in_channels=3,
			out_channels=16,
			kernel_size=8,
			stride=4)
		self.conv2 = nn.Conv2d(
			in_channels=16,
			out_channels=32,
			kernel_size=4,
			stride=2)
		self.fc = nn.Linear(32*9*9 ,num_actions*16)

	def forward(self, x):
		x = F.relu(self.conv1(x))
		x = F.relu(self.conv2(x))
		x = x.view(x.size(0), -1)
		out = F.relu(self.fc(x))
		return out


class FuN(nn.Module):
	def __init__(self, num_actions, horizon):
		super(FuN, self).__init__()
		self.percept = Percept(num_actions)
		self.manager = Manager(num_actions)
		self.worker = Worker(num_actions)
		self.horizon = horizon

	def forward(self, x, m_lstm, w_lstm, goals):
		percept_z = self.percept(x)
		
		m_inputs = (percept_z, m_lstm)
		goal, m_lstm, m_value, m_state = self.manager(m_inputs)

		# todo: at the start, there is no previous goals. Need to be checked
		if goals.sum() == 0:
			goals = goal.unsqueeze(-1)
		else:
			goals = torch.cat([goal.unsqueeze(-1), goals], dim=-1)

		if goals.size(-1) > self.horizon:
			goals = goals[:, :, -self.horizon:]

		w_inputs = (percept_z, w_lstm, goals)
		policy, w_lstm, w_value = self.worker(w_inputs)
		return policy, goal, goals, m_lstm, w_lstm, m_value, w_value, m_state