import numpy as np 
import torch
import torch.nn as nn


from torch.utils.data import Dataset
from torch.autograd import Variable



def MNISTdataLoader(path):
    ##load moving mnist data, data shape = [time steps, batch size, width, height] = [20, batch_size, 64, 64]
    data = np.load(path)
    train = data.transpose(1, 0, 2, 3)
    return train


class MovingMNISTdataset(Dataset):
    # dataset class for moving MNIST data
    def __init__(self, path):
        self.path = path
        self.data = MNISTdataLoader(path)

    def __len__(self):
        return len(self.data[:, 0, 0, 0])

    def __getitem__(self, indx):
        ## getitem method
        self.trainsample_ = self.data[indx, ...]
        self.sample_ = self.trainsample_/255.0

        self.sample = torch.from_numpy(np.expand_dims(self.sample_, axis = 1)).float()
        return self.sample


def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        m.weight.data.normal_(0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        m.weight.data.normal_(1.0, 0.02)
        m.bias.data.fill_(0)


class CGRU_cell(nn.Module):
    """
    ConvGRU Cell
    """
    def __init__(self, shape, input_channels, filter_size, num_features):
        super(CGRU_cell, self).__init__()
        self.shape = shape
        self.input_channels = input_channels
        self.filter_size = filter_size
        self.num_features = num_features
        self.padding = (filter_size-1)//2
        self.conv1 = nn.Conv2d(self.input_channels + self.num_features, 2*self.num_features, self.filter_size, 1, self.padding)
        self.conv2 = nn.Conv2d(self.input_channels + self.num_features, self.num_features, self.filter_size, 1, self.padding)

    def forward(self, input, hidden_state):
        htprev = hidden_state
        combined_1= torch.cat((input, htprev), 1)
        gates = self.conv1(combined_1)

        zgate, rgate = torch.split(gates, self.num_features, dim=1)
        z = torch.sigmoid(zgate)
        r = torch.sigmoid(rgate)

        combined_2 = torch.cat((input, r*htprev), 1)
        ht = self.conv2(combined_2)
        ht = torch.tanh(ht)
        htnext = (1-z)*htprev + z*ht

        return htnext

    def init_hidden(self, batch_size):
        return Variable(torch.zeros(batch_size, self.num_features, self.shape[0], self.shape[1])).cuda()


class CLSTM_cell(nn.Module):
    """ConvLSTMCell
    """
    def __init__(self, shape, input_channels, filter_size, num_features):
        super(CLSTM_cell, self).__init__()

        self.shape = shape ##H, W
        self.input_channels = input_channels
        self.filter_size = filter_size
        self.num_features = num_features
        self.padding = (filter_size - 1)//2
        self.conv = nn.Conv2d(self.input_channels + self.num_features, 4*self.num_features, self.filter_size, 1, self.padding)

    def forward(self, input, hidden_state):

        hx, cx = hidden_state
        combined = torch.cat((input, hx), 1)
        gates = self.conv(combined)  #gates: S, num_features*4, H, W

        ingate, forgetgate, cellgate, outgate = torch.split(gates, self.num_features, dim=1)
        ingate = torch.sigmoid(ingate)
        forgetgate = torch.sigmoid(forgetgate)
        cellgate = torch.tanh(cellgate)
        outgate = torch.sigmoid(outgate)

        cy = (forgetgate*cx) + (ingate*cellgate)
        hy = outgate * torch.tanh(cy)

        return hy, cy

    def init_hidden(self, batch_size):
        return (Variable(torch.zeros(batch_size, self.num_features, self.shape[0], self.shape[1])).cuda(), 
                Variable(torch.zeros(batch_size, self.num_features, self.shape[0], self.shape[1])).cuda())


class CBRNN(nn.Module):
    """Initialize a basic Conv LSTM cell.
    Args:
      shape: int tuple thats the height and width of the hidden states h and c()
      filter_size: int that is the height and width of the filters
      num_features: int that is the num of channels of the states, like hidden_size
      
    """
    def __init__(self, shape, input_chans, filter_size, num_features, cell='CLSTM'):
        super(CBRNN, self).__init__()
        
        self.shape = shape #H,W
        self.input_chans=input_chans
        self.filter_size=filter_size
        self.num_features = num_features
        self.num_layers=len(num_features)
        self.cell = cell

        cell_list = []

        if self.cell == 'CGRU':
            cell_list.append(CGRU_cell(self.shape, self.input_chans, self.filter_size, self.num_features[0]).cuda())

            for idcell in range(1,self.num_layers):
                cell_list.append(CGRU_cell(self.shape, self.num_features[idcell-1]*2, self.filter_size, self.num_features[idcell]).cuda())
            self.cell_list = nn.ModuleList(cell_list)

        else:
            cell_list.append(CLSTM_cell(self.shape, self.input_chans, self.filter_size, self.num_features[0]).cuda())

            for idcell in range(1,self.num_layers):
                cell_list.append(CLSTM_cell(self.shape, self.num_features[idcell-1]*2, self.filter_size, self.num_features[idcell]).cuda())
            self.cell_list = nn.ModuleList(cell_list)


    
    def forward(self, input, hidden_state):
        """
        args:
            hidden_state:list of tuples, one for every layer, each tuple should be hidden_layer_i,c_layer_i
            input is the tensor of shape seq_len,Batch,Chans,H,W
        """

        current_input = input # S,B,C,H,W
        next_hidden=[] # hidden states(h and c)
        seq_len = current_input.size(0)

        
        for idlayer in range(self.num_layers): # loop for every layer

            output_inner = []
            output_inner_fwd = []
            output_inner_bwd = []

            hidden_c = hidden_state[idlayer] #hidden_c=[h,c]   hidden and c are images with several channels
            # forward LSTM
            for t in range(seq_len): # loop for every step
                hidden_c=self.cell_list[idlayer](current_input[t, :, :, :, :],hidden_c)
                if self.cell == 'CLSTM':
                    output_inner_fwd.append(hidden_c)
                else:
                    output_inner_fwd.append(hidden_c)

            hidden_c = hidden_state[idlayer]
            # backward LSTM
            for t in reversed(range(seq_len)):
                hidden_c = self.cell_list[idlayer](current_input[t, :, :, :, :], hidden_c)
                if self.cell == 'CLSTM':
                    output_inner_bwd.insert(0, hidden_c)
                else:
                    output_inner_bwd.insert(0, hidden_c)

            if self.cell == 'CLSTM':
                next_hidden_h = torch.cat((list(output_inner_fwd[-1])[0], list(output_inner_bwd[-1])[0]), dim=1)# the last timestep output o_n
                next_hidden_c = torch.cat((list(output_inner_fwd[-1])[1], list(output_inner_bwd[-1])[1]), dim=1)
                next_hidden.append([next_hidden_h, next_hidden_c])
            else:
                next_hidden.append(torch.cat((output_inner_fwd[-1], output_inner_bwd[-1]), dim=1))

            # merge forward and backward states
            for t in range(seq_len):
                if self.cell == 'CLSTM':
                    output_inner.append(torch.cat((output_inner_fwd[t][0], output_inner_bwd[t][0]), dim=1))
                else:
                    output_inner.append(torch.cat((output_inner_fwd[t], output_inner_bwd[t]), dim=1))
            current_input = torch.cat(output_inner, 0).view(seq_len, *output_inner[0].size()) # S,B,2*hidden_num[idlayer],C,H,W

        # np.save(os.getcwd()+'/debug', next_hidden)
        # exit()
        return next_hidden, current_input

    def init_hidden(self,batch_size):
        init_states=[] # this is a list of tuples
        for i in range(self.num_layers):
            init_states.append(self.cell_list[i].init_hidden(batch_size))
        return init_states


# class ConvBLSTM(nn.Module):
#     # Constructor
#     def __init__(self, shape, in_channels, filter_size, num_features, cell='CLSTM'):
#         super(ConvBLSTM, self).__init__()
#         self.shape = shape  # H,W
#         self.in_channels = in_channels
#         self.filter_size = filter_size
#         self.num_features = num_features
#         self.num_layers = len(num_features)
#         self.cell = cell
#         self.num_features_divide = [num // 2 for num in self.num_features]
#         self.forward_net = CRNN(self.shape, self.in_channels, self.filter_size, self.num_features_divide, self.cell)
#         self.reverse_net = CRNN(self.shape, self.in_channels, self.filter_size, self.num_features_divide, self.cell)
#
#     def forward(self, input,hidden_state):
#         """
#         input = S,B,C,H,W tensors.
#         """
#         reverse_input_idx = list(reversed(range(10)))  # 10为输入序列的长度
#         reverse_input = input[reverse_input_idx, ...]  # reverse input
#         hidden_state_fwd = []
#         hidden_state_rev = []
#         for i in range(self.num_layers):
#             if self.cell == 'CLSTM':
#                 hidden_state_fwd_h , hidden_state_rev_h = torch.chunk(hidden_state[i][0], 2, dim=1)
#                 hidden_state_fwd_c, hidden_state_rev_c = torch.chunk(hidden_state[i][1], 2, dim=1)
#                 hidden_state_fwd.append([hidden_state_fwd_h,hidden_state_fwd_c])
#                 hidden_state_rev.append([hidden_state_rev_h,hidden_state_rev_c])
#             else:
#                 hidden_state_fwd_h, hidden_state_rev_h = torch.chunk(hidden_state[i], 2, dim=1)
#                 hidden_state_fwd.append([hidden_state_fwd_h])
#                 hidden_state_rev.append([hidden_state_rev_h])
#         hidden_state_fwd = tuple(hidden_state_fwd)
#         hidden_state_rev = tuple(hidden_state_rev)
#         y_out_fwd, _ = self.forward_net(input, hidden_state_fwd)
#         y_out_rev, _ = self.reverse_net(reverse_input, hidden_state_rev)
#
#         # reversed_out_idx = list(reversed(range(self.num_layers)))
#         ycat = []
#         for i in range(self.num_layers):
#             if self.cell == 'CLSTM':
#                 reverse_tuples_h = y_out_rev[i][0]  # reverse temporal outputs.
#                 reverse_tuples_c = y_out_rev[i][1]
#                 combine_h = torch.cat((reverse_tuples_h, y_out_fwd[i][0]), dim=1)
#                 combine_c = torch.cat((reverse_tuples_c, y_out_fwd[i][1]), dim=1)
#                 ycat.append([combine_h, combine_c])
#             else:
#                 reverse_tuples_h = y_out_rev[i]
#                 combine_h = torch.cat((reverse_tuples_h, y_out_fwd[i]), dim=1)
#                 ycat.append([combine_h])
#         ycat = tuple(ycat)
#         return ycat  # num_layers*(B, C, H, W)


class MNISTDecoder(nn.Module):
    """
    Decoder for MNIST
    """
    def __init__(self, shape, input_channels, filter_size, num_features):
        super(MNISTDecoder, self).__init__()

        self.shape = shape ##H, W
        self.input_channels = input_channels
        self.filter_size = filter_size
        self.num_features = num_features
        self.padding = (filter_size - 1)//2
        self.conv = nn.Conv2d(self.input_channels, self.num_features, self.filter_size, 1, self.padding)


    def forward(self, state_input_layer):
        """
        Convlutional Decoder for ConvLSTM RNN, forward pass
        """
        inputlayer = torch.cat(state_input_layer, 1)  #inputlayer: B,(2*sum(decoder_num_features)),64,64
        output = self.conv(inputlayer)  # B,1,64,64

        return output  #(B,1,64,64)

class CRNNDecoder(nn.Module):
    """
    Seq2Seq Model Decoder
    """
    def __init__(self, decoderargs, shape, input_chans, filter_size, num_features, cell = "CLSTM"):
        super(CRNNDecoder, self).__init__()

        self.shape = shape  #H,W
        self.input_chans = input_chans
        self.filter_size = filter_size
        self.num_features = num_features
        self.num_layers = len(num_features)
        self.cell = cell
        self.pred_len = 10  #predict the 10 future frames of moving mnist

        cell_list=[]
        
        if self.cell == 'CGRU':
            cell_list.append(CGRU_cell(self.shape, self.input_chans, self.filter_size, self.num_features[0]*2).cuda())
        
            for idcell in range(1,self.num_layers):
                cell_list.append(CGRU_cell(self.shape, self.num_features[idcell-1]*2, self.filter_size, self.num_features[idcell]*2).cuda())
            self.cell_list=nn.ModuleList(cell_list)

        
        else:
            cell_list.append(CLSTM_cell(self.shape, self.input_chans, self.filter_size, self.num_features[0]*2).cuda())

            for idcell in range(1,self.num_layers):
                cell_list.append(CLSTM_cell(self.shape, self.num_features[idcell-1]*2, self.filter_size, self.num_features[idcell]*2).cuda())
            self.cell_list=nn.ModuleList(cell_list) 


        self.decoder_shape = decoderargs[0]
        self.decoder_input_channels = decoderargs[1]
        self.decoder_filter_size = decoderargs[2]
        self.decoder_num_features = decoderargs[3]
        self.decoder = MNISTDecoder(self.decoder_shape, 
                                    self.decoder_input_channels, 
                                    self.decoder_filter_size, 
                                    self.decoder_num_features)
        self.decoder.apply(weights_init)
        self.decoder.cuda()

    def forward(self, hidden_state):
        """
        args:
            hidden_state:list of tuples, one for every layer, each tuple should be hidden_layer_i,c_layer_i
            input is the tensor of shape seq_len,Batch,Chans,H,W
        """

        # current_input = input.transpose(0, 1)#now is seq_len,B,C,H,W
        prediction = []
        hidden_state_all = []
        if self.cell == 'CGRU':
            for idcell in range(self.num_layers):
                tmp = hidden_state[idcell]
                hidden_state_all.append(tmp)
            output = self.decoder(hidden_state_all)
        else:
            for idcell in range(self.num_layers):
                tmp = hidden_state[idcell][0]
                hidden_state_all.append(tmp)
            output = self.decoder(hidden_state_all)  # B,1,64,64

        current_input = output
        prediction.append(output.transpose(0, 1))

        seq_len = self.pred_len - 1
        for t in range(seq_len):
            states = hidden_state
            outputs = []

            for idlayer in range(self.num_layers):
                hidden_c = states[idlayer]  # idlayer=0:  hidden_c[0].shape==(16,?,64,64)
                hidden_c = self.cell_list[idlayer](current_input, hidden_c)
                if self.cell == 'CLSTM':
                    output_inner = hidden_c[0]
                else:
                    output_inner = hidden_c

                outputs.append(output_inner)
                states[idlayer] = hidden_c
                current_input = output_inner

            current_input = self.decoder(outputs)
            prediction.append(current_input.transpose(0, 1))

        return prediction


    def init_hidden(self,batch_size):

        init_states = []  # this is a list of tuples
        for i in range(self.num_layers):
            init_states.append(self.cell_list[i].init_hidden(batch_size))
        return init_states


class PredModel(nn.Module):
    """
    Overall model with both encoder and decoder part
    """
    def __init__(self, CRNNargs, decoderargs, cell = 'CLSTM'):
        super(PredModel, self).__init__()

        self.cell = cell

        self.conv_rnn_shape = CRNNargs[0]
        self.conv_rnn_inp_chans = CRNNargs[1]
        self.conv_rnn_filter_size = CRNNargs[2]
        self.conv_rnn_num_features = CRNNargs[3]
        self.conv_rnn = CBRNN(self.conv_rnn_shape,
                             self.conv_rnn_inp_chans,
                             self.conv_rnn_filter_size,
                             self.conv_rnn_num_features,
                             self.cell)
        self.conv_rnn.apply(weights_init)
        self.conv_rnn.cuda()

        self.decoder_args = decoderargs
        self.seq2seq_decoder = CRNNDecoder(self.decoder_args, 
                                            self.conv_rnn_shape, 
                                            self.conv_rnn_inp_chans,
                                            self.conv_rnn_filter_size,
                                            self.conv_rnn_num_features,
                                            self.cell)
        self.seq2seq_decoder.apply(weights_init)
        self.conv_rnn.cuda()
        # self.decoder_shape = decoderargs[0]
        # self.decoder_num_features = decoderargs[1]
        # self.decoder_filter_size = decoderargs[2]
        # self.decoder_stride = decoderargs[3]
        # self.decoder = MNISTDecoder(self.decoder_shape, 
        #                             self.decoder_num_features, 
        #                             self.decoder_filter_size, 
        #                             self.decoder_stride)
        # self.decoder.apply(weights_init)
        # self.decoder.cuda()

    def forward(self, input, hidden_state):
        input_transpose = input.transpose(0, 1)  #B,S,C,H,W -> S,B,C,H,W
        out = self.conv_rnn(input_transpose, hidden_state)  #
        #pred = self.seq2seq_decoder([out[0][0], out[0][1]]) #   out[0][0] = ( B,hidden_num[0],64,64 ; B,hidden_num[0],64,64)
                                                            #   out[0][1] = ( B,hidden_num[1],64,64 ; B,hidden_num[1],64,64)

        #pred = self.seq2seq_decoder(out[0])                 #  out[0] = {out[0][0],out[0][1],out[0][2]...,out[0][nlayers]}
        pred = self.seq2seq_decoder(out[0])
        return pred

    def init_hidden(self, batch_size):
        init_states = self.conv_rnn.init_hidden(batch_size)
        return init_states

def crossentropyloss(pred, target):
    loss = -torch.sum(torch.log(pred)*target + torch.log(1-pred)*(1-target))
    return loss
