from mininet.topo import Topo
class MyTopo(Topo):
	def __init__(self):
	    Topo.__init__(self)
        
	    s=[]
	   
	    s.append(self.addSwitch('s1'))
	    s.append(self.addSwitch('s2'))
	    
	    self.addLink(s[0],s[1])

	    h1=self.addHost('h1',mac='00:00:00:00:00:01')
	    h2=self.addHost('h2',mac='00:00:00:00:00:02')
		
	    self.addLink(s[0],h1)
	    self.addLink(s[1],h2)

topos={'mytopo':(lambda:MyTopo())}

# mn --mac --custom /home/hl/text/topo3.py --topo mytopo --switch ovsk,protocols=OpenFlow13 --controller remote -x
